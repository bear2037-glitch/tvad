import streamlit as st
import pandas as pd
import io
import re
import datetime
import hashlib
import json
from supabase import create_client, Client

st.set_page_config(page_title="편성현황 관리", page_icon="📋", layout="wide")

# ── 비밀번호 게이트 ───────────────────────────────────────────────────────

def _auth_token() -> str:
    pw = st.secrets.get("APP_PASSWORD", "")
    return hashlib.sha256(pw.encode()).hexdigest()[:24]

def check_password() -> bool:
    token = _auth_token()
    # URL 쿼리 파라미터 체크 (서버 재시작 후에도 유지됨)
    if st.query_params.get("auth") == token:
        st.session_state.authenticated = True
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("📋 편성현황 관리 시스템")
    pw = st.text_input("비밀번호를 입력하세요", type="password")
    if st.button("확인", type="primary"):
        if pw == st.secrets.get("APP_PASSWORD", ""):
            st.session_state.authenticated = True
            st.query_params["auth"] = token
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    return False

if not check_password():
    st.stop()

# ── 상수 ─────────────────────────────────────────────────────────────────

TABLE = "tvad_schedule"
CONFIG_TABLE = "tvad_config"

# Supabase 컬럼명 ↔ 앱 컬럼명
DB_TO_APP: dict[str, str] = {
    "date_str":     "날짜",
    "mgmt_no":      "관리번호",
    "m_code":       "M code",
    "item_name":    "아이템명",
    "main_copy":    "메인카피",
    "sub_copy":     "서브카피",
    "slot":         "구좌",
    "check_result": "체크_결과",
}
APP_TO_DB: dict[str, str] = {v: k for k, v in DB_TO_APP.items()}
APP_COL_ORDER = list(DB_TO_APP.values())

DEFAULT_GROUPS = [
    {"name": "A그룹", "keywords": ["MD선정추천", "TV인기상품"]},
    {"name": "B그룹", "keywords": ["카테고리MD추천", "카테고리베스트"]},
]


# ── Supabase 클라이언트 ───────────────────────────────────────────────────

@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# ── 그룹 설정 로드/저장 ──────────────────────────────────────────────────

def load_groups_from_db() -> list[dict]:
    try:
        res = (
            get_supabase()
            .table(CONFIG_TABLE)
            .select("value")
            .eq("key", "restricted_groups")
            .execute()
        )
        if res.data:
            return json.loads(res.data[0]["value"])
    except Exception:
        pass
    return DEFAULT_GROUPS


def save_groups_to_db(groups: list[dict]):
    get_supabase().table(CONFIG_TABLE).upsert({
        "key": "restricted_groups",
        "value": json.dumps(groups, ensure_ascii=False),
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }).execute()


# ── DB 조작 ──────────────────────────────────────────────────────────────

def load_from_db() -> pd.DataFrame | None:
    try:
        res = (
            get_supabase()
            .table(TABLE)
            .select("*")
            .order("date_str")
            .order("slot")
            .order("m_code")
            .execute()
        )
        if not res.data:
            return None
        df = pd.DataFrame(res.data).rename(columns=DB_TO_APP)
        cols = APP_COL_ORDER + ["id"]
        return df[[c for c in cols if c in df.columns]].reset_index(drop=True)
    except Exception as e:
        st.error(f"DB 로드 실패: {e}")
        return None


def _build_records(df: pd.DataFrame, mcode_override: dict[str, str] | None = None) -> list[dict]:
    placeholders = {"추후전달", "", "nan", "none"}
    records = []
    for _, row in df.iterrows():
        mgmt_no = row["관리번호"]
        m = str(row["M code"]).strip()
        if mcode_override and m.lower() in placeholders and mgmt_no in mcode_override:
            m = mcode_override[mgmt_no]
        records.append({
            "date_str":     row["날짜"],
            "mgmt_no":      mgmt_no,
            "m_code":       m,
            "item_name":    row["아이템명"],
            "main_copy":    row["메인카피"],
            "sub_copy":     row["서브카피"],
            "slot":         row["구좌"],
            "check_result": row["체크_결과"],
        })
    return records


def upsert_to_db(df: pd.DataFrame) -> tuple[int, int]:
    """관리번호 단위 upsert. (업데이트 수, 신규 수) 반환."""
    sb = get_supabase()
    mgmt_nos = df["관리번호"].unique().tolist()

    # 기존에 실제 입력된 M code 보존 (추후전달 → 실제 코드로 덮어쓰기 방지)
    mcode_override: dict[str, str] = {}
    if st.session_state.result_df is not None:
        placeholders = {"추후전달", "", "nan", "none"}
        for _, row in st.session_state.result_df[
            st.session_state.result_df["관리번호"].isin(mgmt_nos)
        ].iterrows():
            m = str(row["M code"]).strip()
            mgmt = row["관리번호"]
            if mgmt not in mcode_override and m.lower() not in placeholders:
                mcode_override[mgmt] = m

    # 기존에 있던 관리번호 파악 (업데이트 vs 신규 구분)
    existing_nos = set()
    if st.session_state.result_df is not None:
        existing_nos = set(st.session_state.result_df["관리번호"].unique())
    n_updated = len([m for m in mgmt_nos if m in existing_nos])
    n_inserted = len(mgmt_nos) - n_updated

    # 해당 관리번호 삭제 후 신규 삽입
    for i in range(0, len(mgmt_nos), 50):
        sb.table(TABLE).delete().in_("mgmt_no", mgmt_nos[i : i + 50]).execute()

    records = _build_records(df, mcode_override)
    for i in range(0, len(records), 100):
        sb.table(TABLE).insert(records[i : i + 100]).execute()

    return n_updated, n_inserted


def clear_all_db():
    """전체 초기화 (주의: 복구 불가)."""
    get_supabase().table(TABLE).delete().gte("id", 1).execute()


def update_mcode_single(row_id: int, code: str):
    get_supabase().table(TABLE).update({"m_code": code}).eq("id", row_id).execute()
    st.session_state.result_df.loc[
        st.session_state.result_df["id"] == row_id, "M code"
    ] = code


def update_mcode_bulk(mgmt_no: str, code: str):
    get_supabase().table(TABLE).update({"m_code": code}).eq("mgmt_no", mgmt_no).execute()
    st.session_state.result_df.loc[
        st.session_state.result_df["관리번호"] == mgmt_no, "M code"
    ] = code


# ── 파싱 ─────────────────────────────────────────────────────────────────

def parse_schedule(raw_text: str, groups: list[dict] | None = None) -> tuple[pd.DataFrame | None, list[str]]:
    errors: list[str] = []

    # BOM 제거 및 정규화
    raw_text = raw_text.lstrip("﻿").strip()

    # 헤더 자동 감지: 첫 줄에 '관리번호'가 없으면 헤더 자동 추가
    DEFAULT_HEADER = "관리번호\tM code\t메인카피\t서브카피\t아이템명\t비고"
    first_line_cols = [c.strip() for c in raw_text.split("\n")[0].split("\t")]
    if "관리번호" not in first_line_cols:
        raw_text = DEFAULT_HEADER + "\n" + raw_text

    try:
        df_origin = pd.read_csv(io.StringIO(raw_text), sep="\t")
    except Exception as e:
        return None, [f"데이터 로드 실패: {e}"]

    # 컬럼명 앞뒤 공백 제거
    df_origin.columns = df_origin.columns.str.strip()

    required = ["관리번호", "M code", "아이템명", "비고"]
    missing = [c for c in required if c not in df_origin.columns]
    if missing:
        detected = list(df_origin.columns)
        if len(detected) == 1:
            preview = detected[0][:80]
            return None, [
                f"탭(Tab) 구분이 감지되지 않았습니다.\n"
                f"첫 번째 행 미리보기: 「{preview}」\n"
                f"엑셀에서 셀을 선택 후 Ctrl+C → 여기에 Ctrl+V 하셨나요?"
            ]
        return None, [
            f"필수 컬럼 없음: {missing}\n"
            f"감지된 컬럼 ({len(detected)}개): {detected}\n"
            f"← 컬럼 순서: 관리번호 / M code / 메인카피 / 서브카피 / 아이템명 / 비고"
        ]

    df_origin["M code"] = df_origin["M code"].fillna("").astype(str).str.strip()
    for col in ["메인카피", "서브카피"]:
        if col in df_origin.columns:
            df_origin[col] = df_origin[col].fillna("").astype(str).replace(["nan", "NaN", "None"], "")

    refined = []
    for _, row in df_origin.iterrows():
        note_raw = str(row.get("비고", "")).strip()
        관리번호 = str(row["관리번호"]).strip()

        if note_raw in ("nan", "NaN", "None", ""):
            errors.append(f"⚠ 비고 없음 → 관리번호 {관리번호}")
            continue

        note = re.sub(r"\s*:\s*", ":", note_raw)
        note = re.sub(r"(\d)\s+([가-힣a-zA-Z].*?:)", r"\1, \2", note)
        note = re.sub(r"(\d)([가-힣a-zA-Z].*?[①-⑳\d]*?:)", r"\1, \2", note)
        note = re.sub(r"([가-힣a-zA-Z①-⑳])\s+(\d{1,2}/\d{1,2})", r"\1:\2", note)

        parts = [p.strip() for p in note.split(",")]
        processed_parts: list[str] = []
        last_ad_name = ""

        for p in parts:
            if ":" in p:
                ad_part, date_part = p.split(":", 1)
                if re.match(r"^([①-⑳\d\s]+)$", ad_part.strip()) and last_ad_name:
                    full_ad = last_ad_name + ad_part.strip()
                else:
                    full_ad = ad_part.strip()
                    last_ad_name = re.sub(r"[①-⑳\d\s]+$", "", full_ad)
                processed_parts.append(f"{full_ad}:{date_part}")
            elif processed_parts:
                processed_parts[-1] += "," + p

        final_note = ", ".join(processed_parts)
        pairs = re.findall(r"([^:]+):([\d/,\s]+)(?:,|$)", final_note)

        if not pairs:
            errors.append(f"⚠ 파싱 실패 → 관리번호: {관리번호} | 비고: 「{note_raw}」")
            continue

        for ad, dates in pairs:
            ad_cleaned = ad.strip().strip(",").replace(" ", "").upper()
            date_list = [d.strip() for d in dates.split(",") if d.strip()]
            if not date_list:
                continue
            base_month = date_list[0].split("/")[0]
            for d in date_list:
                m, day = d.split("/") if "/" in d else (base_month, d)
                year = 2025 if m == "12" else 2026
                try:
                    dt = datetime.date(year, int(m), int(day))
                    refined.append({
                        "날짜":    dt.strftime("%Y-%m-%d"),
                        "관리번호": 관리번호,
                        "M code":  row["M code"],
                        "아이템명": str(row.get("아이템명", "")).strip(),
                        "메인카피": str(row.get("메인카피", "")).strip(),
                        "서브카피": str(row.get("서브카피", "")).strip(),
                        "구좌":    ad_cleaned,
                        "체크_결과": "",
                    })
                except ValueError as e:
                    errors.append(f"⚠ 날짜 오류 → {관리번호}: {d} ({e})")

    if not refined:
        return None, errors

    result_df = pd.DataFrame(refined).sort_values(["날짜", "구좌", "M code"]).reset_index(drop=True)

    # 중복 체크
    dup_mask = result_df.duplicated(subset=["날짜", "구좌"], keep=False)
    result_df.loc[dup_mask, "체크_결과"] = "⚠️구좌중복"

    active_groups = {g["name"]: g["keywords"] for g in (groups or [])}
    result_df["__group"] = result_df["구좌"].apply(
        lambda ad: next((g for g, kws in active_groups.items() if any(k in ad for k in kws)), None)
    )
    valid = result_df["M code"].str.isdigit() & result_df["__group"].notna()
    grp_dup = result_df[valid].duplicated(subset=["날짜", "__group", "M code"], keep=False)
    for idx, is_dup in grp_dup.items():
        if is_dup:
            g = result_df.loc[idx, "__group"]
            cur = result_df.loc[idx, "체크_결과"]
            msg = f"🚫{g}_M코드중복"
            result_df.loc[idx, "체크_결과"] = msg if not cur else f"{cur} / {msg}"

    return result_df.drop(columns=["__group"]), errors


# ── 엑셀 출력 ────────────────────────────────────────────────────────────

def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    export_df = df.drop(columns=["id"], errors="ignore")
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        export_df.to_excel(writer, sheet_name="상세내역", index=False)
        matrix = export_df.copy()
        matrix["표시"] = matrix["아이템명"] + "\n" + matrix["M code"]
        try:
            pivot = matrix.pivot_table(
                index="구좌", columns="날짜", values="표시",
                aggfunc=lambda x: "\n---\n".join(x.astype(str)),
            )
            pivot.to_excel(writer, sheet_name="편성대시보드")
            wb = writer.book
            ws = writer.sheets["편성대시보드"]
            fmt = wb.add_format({"text_wrap": True, "valign": "vcenter", "align": "center", "border": 1, "font_size": 9})
            ws.set_column(0, 0, 25, fmt)
            ws.set_column(1, len(pivot.columns), 20, fmt)
            ws.set_default_row(55)
        except Exception:
            pass
    return buf.getvalue()


# ── 세션 초기화 ──────────────────────────────────────────────────────────

if "result_df" not in st.session_state:
    st.session_state.result_df = load_from_db()
if "parse_errors" not in st.session_state:
    st.session_state.parse_errors = []
if "pending_bulk" not in st.session_state:
    st.session_state.pending_bulk = []
if "restricted_groups" not in st.session_state:
    st.session_state.restricted_groups = load_groups_from_db()
if "highlight_mgmt_nos" not in st.session_state:
    st.session_state.highlight_mgmt_nos = set()


# ── 일괄 업데이트 다이얼로그 ─────────────────────────────────────────────

@st.dialog("🔄 일괄 업데이트 확인")
def bulk_confirm_dialog(change: dict):
    관리번호 = change["관리번호"]
    m_code = change["M code"]
    df = st.session_state.result_df
    affected = df[df["관리번호"] == 관리번호][["날짜", "구좌", "아이템명"]].reset_index(drop=True)

    st.markdown(f"관리번호 **`{관리번호}`** 는 총 **{len(affected)}개 구좌**에 편성되어 있습니다.")
    st.markdown(f"M code **`{m_code}`** 를 어떻게 적용할까요?")
    st.dataframe(affected, use_container_width=True, hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button(f"✅ 전체 {len(affected)}개 구좌 적용", type="primary", use_container_width=True):
            with st.spinner("DB 업데이트 중..."):
                update_mcode_bulk(관리번호, m_code)
            st.session_state.pending_bulk.pop(0)
            st.rerun()
    with col2:
        if st.button("이 셀만 적용", use_container_width=True):
            with st.spinner("DB 업데이트 중..."):
                update_mcode_single(change["id"], m_code)
            st.session_state.pending_bulk.pop(0)
            st.rerun()


# ── 사이드바: 구좌 필터 ──────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 구좌 필터")
    if st.session_state.result_df is not None:
        _df = st.session_state.result_df
        all_slots = sorted(_df["구좌"].unique().tolist())
        slot_counts = _df["구좌"].value_counts()

        # 초기화 (데이터가 바뀌면 전체 선택으로 리셋)
        if "slot_filter" not in st.session_state:
            st.session_state.slot_filter = all_slots[:]

        # 그룹 매핑 (색상 구분용)
        grp_map: dict[str, str] = {}
        for g in st.session_state.restricted_groups:
            for s in all_slots:
                if s not in grp_map and any(kw in s for kw in g["keywords"]):
                    grp_map[s] = g["name"]

        c1, c2 = st.columns(2)
        if c1.button("전체 선택", use_container_width=True):
            st.session_state.slot_filter = all_slots[:]
            st.rerun()
        if c2.button("전체 해제", use_container_width=True):
            st.session_state.slot_filter = []
            st.rerun()

        # 구좌 목록 테이블
        slot_info = pd.DataFrame([
            {
                "구좌": s,
                "건수": int(slot_counts.get(s, 0)),
                "그룹": grp_map.get(s, "—"),
            }
            for s in all_slots
        ])
        st.dataframe(
            slot_info,
            use_container_width=True,
            hide_index=True,
            height=min(38 * len(all_slots) + 38, 340),
        )

        # 현재 선택에 없는 구좌는 제거
        valid_sel = [s for s in st.session_state.slot_filter if s in all_slots]
        if len(valid_sel) != len(st.session_state.slot_filter):
            st.session_state.slot_filter = all_slots[:]

        st.multiselect(
            "보고 싶은 구좌만 선택",
            options=all_slots,
            format_func=lambda s: f"{s}  ({int(slot_counts.get(s,0))}건)",
            key="slot_filter",
        )
        st.caption(f"선택: {len(st.session_state.slot_filter)} / {len(all_slots)}개")
    else:
        st.caption("데이터를 먼저 파싱해주세요.")


# ── 메인 UI ──────────────────────────────────────────────────────────────

st.title("📋 편성현황 관리 시스템")

tab1, tab2, tab3, tab4 = st.tabs(["📥 데이터 입력", "📊 편성대시보드", "✏️ 상세내역 & M code 수정", "⚙️ 그룹 설정"])

# ══ TAB 1 ════════════════════════════════════════════════════════════════
with tab1:
    st.caption("엑셀에서 데이터 행만 선택(헤더 없어도 됨) → Ctrl+C 후 아래에 Ctrl+V")
    st.caption("컬럼 순서: **관리번호 / M code / 메인카피 / 서브카피 / 아이템명 / 비고**")
    raw_input = st.text_area(
        "원본 데이터",
        height=280,
        placeholder="M_26_0893\t77799317\t\t\t에어룸냉감침구\t특가오늘⑨:6/20, TV인기상품⑫:6/17\n...",
        label_visibility="collapsed",
    )

    if st.button("📊 파싱 실행", type="primary"):
        if not raw_input.strip():
            st.warning("데이터를 붙여넣어 주세요.")
        else:
            new_df, errors = parse_schedule(raw_input, st.session_state.restricted_groups)

            if new_df is None:
                st.error("파싱 실패 — 아래 경고를 확인해주세요.")
            else:
                try:
                    with st.spinner("DB 업데이트 중..."):
                        n_upd, n_new = upsert_to_db(new_df)
                        st.session_state.result_df = load_from_db()
                    new_mgmt_nos = set(new_df["관리번호"].unique())
                    st.session_state.highlight_mgmt_nos = new_mgmt_nos
                    st.session_state.parse_errors = errors
                    st.session_state.pending_bulk = []
                    st.session_state.pop("slot_filter", None)

                    parts = []
                    if n_new:   parts.append(f"신규 {n_new}개")
                    if n_upd:   parts.append(f"업데이트 {n_upd}개")
                    label = " / ".join(parts) if parts else f"{len(new_mgmt_nos)}개"
                    st.success(f"✅ 관리번호 {label} 처리 완료 ({len(new_df)}건)")

                    df_loaded = st.session_state.result_df
                    dup_count = int((df_loaded["체크_결과"] != "").sum()) if df_loaded is not None else 0
                    if dup_count:
                        st.warning(f"중복 감지: {dup_count}건 → '상세내역' 탭에서 확인하세요.")
                except Exception as e:
                    st.error(f"DB 저장 실패: {e}")

            if errors:
                with st.expander(f"⚠️ 파싱 경고 {len(errors)}건", expanded=(new_df is None)):
                    for err in errors:
                        st.warning(err)

    if st.session_state.result_df is not None:
        st.info(f"현재 데이터: {len(st.session_state.result_df)}건 로드됨")

    with st.expander("⚠️ 전체 데이터 초기화 (주의)"):
        st.warning("DB의 모든 데이터가 삭제됩니다. 복구 불가합니다.")
        if st.button("전체 삭제", type="secondary"):
            clear_all_db()
            st.session_state.result_df = None
            st.session_state.highlight_mgmt_nos = set()
            st.session_state.pop("slot_filter", None)
            st.success("전체 데이터가 삭제되었습니다.")
            st.rerun()


# ══ TAB 2 ════════════════════════════════════════════════════════════════
with tab2:
    if st.session_state.result_df is None:
        st.info("'데이터 입력' 탭에서 데이터를 먼저 파싱해주세요.")
    else:
        df = st.session_state.result_df

        # 구좌 필터 적용
        sel = st.session_state.get("slot_filter", None)
        df_view = df[df["구좌"].isin(sel)] if sel is not None else df

        if df_view.empty:
            st.warning("선택된 구좌가 없습니다. 사이드바에서 구좌를 선택해주세요.")
        else:
            recently = st.session_state.highlight_mgmt_nos
            matrix = df_view.copy()
            matrix["표시"] = matrix["아이템명"] + "\n" + matrix["M code"]
            if recently:
                is_new = matrix["관리번호"].isin(recently)
                matrix.loc[is_new, "표시"] = "★ " + matrix.loc[is_new, "표시"]
            has_issue = matrix["체크_결과"] != ""
            matrix.loc[has_issue, "표시"] += "\n" + matrix.loc[has_issue, "체크_결과"]

            try:
                pivot = matrix.pivot_table(
                    index="구좌", columns="날짜", values="표시",
                    aggfunc=lambda x: " / ".join(x.astype(str)),
                )
                st.dataframe(pivot, use_container_width=True, height=500)
            except Exception as e:
                st.error(f"대시보드 오류: {e}")

        st.download_button(
            "📥 엑셀 다운로드",
            data=to_excel(df),
            file_name=f"편성현황_{datetime.date.today()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# ══ TAB 3 ════════════════════════════════════════════════════════════════
with tab3:
    if st.session_state.result_df is None:
        st.info("'데이터 입력' 탭에서 데이터를 먼저 파싱해주세요.")
    else:
        if st.session_state.pending_bulk:
            bulk_confirm_dialog(st.session_state.pending_bulk[0])

        st.caption("**M code** 컬럼만 수정 가능합니다. 수정 후 반드시 **저장** 버튼을 눌러주세요.")

        # 구좌 필터 적용
        sel3 = st.session_state.get("slot_filter", None)
        base_df = st.session_state.result_df
        df_display = (base_df[base_df["구좌"].isin(sel3)] if sel3 is not None else base_df).drop(columns=["id"], errors="ignore").copy()

        # ★ 하이라이트 열 추가 (최근 업데이트 행 → 상단 정렬)
        recently = st.session_state.highlight_mgmt_nos
        df_display.insert(0, "★", df_display["관리번호"].isin(recently).map({True: "★", False: ""}))
        df_display = df_display.sort_values("★", ascending=False)  # index 보존 (reset_index 안 함)

        readonly_cols = [c for c in df_display.columns if c != "M code"]

        edited_df = st.data_editor(
            df_display,
            disabled=readonly_cols,
            use_container_width=True,
            height=500,
            hide_index=True,
            column_config={
                "★":        st.column_config.TextColumn("★", width="small"),
                "M code":   st.column_config.TextColumn("M code ✏️", max_chars=30),
                "날짜":     st.column_config.TextColumn("날짜", width="small"),
                "체크_결과": st.column_config.TextColumn("체크결과", width="medium"),
            },
            key="detail_editor",
        )

        if st.button("💾 저장", type="primary"):
            original = st.session_state.result_df
            # display index → original index 매핑
            display_index = df_display.index
            changes: list[dict] = []

            for pos, orig_idx in enumerate(display_index):
                관리번호 = str(edited_df.iloc[pos]["관리번호"]).strip()
                if not 관리번호 or 관리번호 in ("nan", "None", ""):
                    continue

                orig_mcode = str(original.loc[orig_idx, "M code"]).strip()
                new_mcode  = str(edited_df.iloc[pos]["M code"]).strip()

                if new_mcode != orig_mcode:
                    changes.append({
                        "orig_idx": orig_idx,
                        "id":       int(original.loc[orig_idx, "id"]),
                        "관리번호":  관리번호,
                        "M code":   new_mcode,
                    })

            if not changes:
                st.info("변경된 내용이 없습니다.")
            else:
                seen: dict[str, dict] = {}
                for c in changes:
                    seen[c["관리번호"]] = c
                changes = list(seen.values())

                needs_confirm, auto_apply = [], []
                for change in changes:
                    count = len(original[original["관리번호"] == change["관리번호"]])
                    (needs_confirm if count > 1 else auto_apply).append(change)

                if auto_apply:
                    with st.spinner("저장 중..."):
                        for c in auto_apply:
                            update_mcode_single(c["id"], c["M code"])

                if needs_confirm:
                    st.session_state.pending_bulk = needs_confirm
                    st.rerun()
                else:
                    st.success(f"✅ {len(auto_apply)}건 저장 완료")
                    st.rerun()


# ══ TAB 4 ════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("중복 그룹 설정")
    st.caption(
        "같은 그룹 내에서 동일 날짜에 동일 M code가 중복되면 🚫 경고가 뜹니다. "
        "키워드는 구좌명에 **포함**되면 해당 그룹으로 인식합니다."
    )

    groups = st.session_state.restricted_groups
    df_groups = pd.DataFrame([
        {"그룹명": g["name"], "구좌 키워드 (쉼표로 구분)": ", ".join(g["keywords"])}
        for g in groups
    ])

    edited_groups = st.data_editor(
        df_groups,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "그룹명": st.column_config.TextColumn("그룹명", width="small"),
            "구좌 키워드 (쉼표로 구분)": st.column_config.TextColumn(
                "구좌 키워드 (쉼표로 구분)", width="large",
                help="예: MD선정추천, TV인기상품"
            ),
        },
    )

    if st.button("💾 그룹 설정 저장", type="primary"):
        new_groups = []
        for _, row in edited_groups.iterrows():
            name = str(row["그룹명"]).strip()
            raw_kw = str(row["구좌 키워드 (쉼표로 구분)"]).strip()
            kws = [k.strip() for k in raw_kw.split(",") if k.strip() and k.strip() not in ("nan", "None")]
            if name and name not in ("nan", "None") and kws:
                new_groups.append({"name": name, "keywords": kws})
        if not new_groups:
            st.warning("저장할 그룹이 없습니다. 그룹명과 키워드를 입력해주세요.")
        else:
            try:
                save_groups_to_db(new_groups)
                st.session_state.restricted_groups = new_groups
                st.success(f"✅ {len(new_groups)}개 그룹 저장 완료")
            except Exception as e:
                st.error(f"저장 실패: {e}\n\nSupabase에 tvad_config 테이블이 없으면 setup.sql을 먼저 실행해주세요.")
