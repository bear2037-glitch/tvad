import streamlit as st
import pandas as pd
import io
import re
import datetime
import hashlib
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

# Supabase 컬럼명 ↔ 앱 컬럼명
DB_TO_APP: dict[str, str] = {
    "date_str":     "날짜",
    "mgmt_no":      "관리번호",
    "m_code":       "M code",
    "item_name":    "아이템명",
    "main_copy":    "메인카피",
    "sub_copy":     "서브카피",
    "slot":         "구좌",
    "product_code": "상품코드",
    "check_result": "체크_결과",
}
APP_TO_DB: dict[str, str] = {v: k for k, v in DB_TO_APP.items()}
APP_COL_ORDER = list(DB_TO_APP.values())

RESTRICTED_GROUPS = {
    "메인그룹":    ["MD선정추천", "TV인기상품"],
    "카테고리그룹": ["카테고리MD추천", "카테고리베스트"],
}


# ── Supabase 클라이언트 ───────────────────────────────────────────────────

@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


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


def save_all_to_db(df: pd.DataFrame):
    sb = get_supabase()
    # 전체 교체: 기존 삭제 후 신규 insert
    sb.table(TABLE).delete().gte("id", 1).execute()
    records = [
        {
            "date_str":     row["날짜"],
            "mgmt_no":      row["관리번호"],
            "m_code":       row["M code"],
            "item_name":    row["아이템명"],
            "main_copy":    row["메인카피"],
            "sub_copy":     row["서브카피"],
            "slot":         row["구좌"],
            "product_code": str(row.get("상품코드", "") or ""),
            "check_result": row["체크_결과"],
        }
        for _, row in df.iterrows()
    ]
    for i in range(0, len(records), 100):
        sb.table(TABLE).insert(records[i : i + 100]).execute()


def update_code_single(row_id: int, code: str):
    get_supabase().table(TABLE).update({"product_code": code}).eq("id", row_id).execute()
    st.session_state.result_df.loc[
        st.session_state.result_df["id"] == row_id, "상품코드"
    ] = code


def update_code_bulk(mgmt_no: str, code: str):
    get_supabase().table(TABLE).update({"product_code": code}).eq("mgmt_no", mgmt_no).execute()
    st.session_state.result_df.loc[
        st.session_state.result_df["관리번호"] == mgmt_no, "상품코드"
    ] = code


# ── 파싱 ─────────────────────────────────────────────────────────────────

def parse_schedule(raw_text: str) -> tuple[pd.DataFrame | None, list[str]]:
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
                        "상품코드": "",
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

    result_df["__group"] = result_df["구좌"].apply(
        lambda ad: next((g for g, kws in RESTRICTED_GROUPS.items() if any(k in ad for k in kws)), None)
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
        matrix["표시"] = matrix["아이템명"] + "\n(" + matrix["M code"] + ")"
        has_code = matrix["상품코드"].str.strip() != ""
        matrix.loc[has_code, "표시"] += "\n[" + matrix.loc[has_code, "상품코드"] + "]"
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


# ── 일괄 업데이트 다이얼로그 ─────────────────────────────────────────────

@st.dialog("🔄 일괄 업데이트 확인")
def bulk_confirm_dialog(change: dict):
    관리번호 = change["관리번호"]
    상품코드 = change["상품코드"]
    df = st.session_state.result_df
    affected = df[df["관리번호"] == 관리번호][["날짜", "구좌", "아이템명"]].reset_index(drop=True)

    st.markdown(f"관리번호 **`{관리번호}`** 는 총 **{len(affected)}개 구좌**에 편성되어 있습니다.")
    st.markdown(f"상품코드 **`{상품코드}`** 를 어떻게 적용할까요?")
    st.dataframe(affected, use_container_width=True, hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button(f"✅ 전체 {len(affected)}개 구좌 적용", type="primary", use_container_width=True):
            with st.spinner("DB 업데이트 중..."):
                update_code_bulk(관리번호, 상품코드)
            st.session_state.pending_bulk.pop(0)
            st.rerun()
    with col2:
        if st.button("이 셀만 적용", use_container_width=True):
            with st.spinner("DB 업데이트 중..."):
                update_code_single(change["id"], 상품코드)
            st.session_state.pending_bulk.pop(0)
            st.rerun()


# ── 메인 UI ──────────────────────────────────────────────────────────────

st.title("📋 편성현황 관리 시스템")

tab1, tab2, tab3 = st.tabs(["📥 데이터 입력", "📊 편성대시보드", "✏️ 상세내역 & 코드 입력"])

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
            new_df, errors = parse_schedule(raw_input)

            if new_df is None:
                st.error("파싱 실패 — 아래 경고를 확인해주세요.")
            else:
                # 기존 상품코드 보존 (관리번호 기준)
                if st.session_state.result_df is not None:
                    old_codes = (
                        st.session_state.result_df
                        .groupby("관리번호")["상품코드"].first().to_dict()
                    )
                    new_df["상품코드"] = new_df["관리번호"].map(old_codes).fillna("")

                try:
                    with st.spinner("DB 저장 중..."):
                        save_all_to_db(new_df)
                        st.session_state.result_df = load_from_db()
                    st.session_state.parse_errors = errors
                    st.session_state.pending_bulk = []

                    df_loaded = st.session_state.result_df
                    dup_count = int((df_loaded["체크_결과"] != "").sum()) if df_loaded is not None else 0
                    st.success(f"✅ 파싱 완료: {len(new_df)}건 DB 저장됨")
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


# ══ TAB 2 ════════════════════════════════════════════════════════════════
with tab2:
    if st.session_state.result_df is None:
        st.info("'데이터 입력' 탭에서 데이터를 먼저 파싱해주세요.")
    else:
        df = st.session_state.result_df
        matrix = df.copy()
        matrix["표시"] = matrix["아이템명"]
        has_code = matrix["상품코드"].str.strip() != ""
        matrix.loc[has_code, "표시"] += " [" + matrix.loc[has_code, "상품코드"] + "]"
        has_issue = df["체크_결과"] != ""
        matrix.loc[has_issue, "표시"] += " " + df.loc[has_issue, "체크_결과"]

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

        st.caption("**상품코드** 컬럼만 입력/수정 가능합니다. 수정 후 반드시 **저장** 버튼을 눌러주세요.")

        df_display = st.session_state.result_df.drop(columns=["id"], errors="ignore")
        readonly_cols = [c for c in df_display.columns if c != "상품코드"]

        edited_df = st.data_editor(
            df_display,
            disabled=readonly_cols,
            use_container_width=True,
            height=500,
            hide_index=True,
            column_config={
                "상품코드": st.column_config.TextColumn("상품코드 ✏️", max_chars=50),
                "날짜":    st.column_config.TextColumn("날짜", width="small"),
                "체크_결과": st.column_config.TextColumn("체크결과", width="medium"),
            },
            key="detail_editor",
        )

        if st.button("💾 저장", type="primary"):
            original = st.session_state.result_df
            changes: list[dict] = []

            for idx in edited_df.index:
                관리번호 = str(edited_df.loc[idx, "관리번호"]).strip()
                if not 관리번호 or 관리번호 in ("nan", "None", ""):
                    continue

                orig_code = str(original.loc[idx, "상품코드"]).strip()
                new_code  = str(edited_df.loc[idx, "상품코드"]).strip()

                if new_code != orig_code:
                    changes.append({
                        "idx":   idx,
                        "id":    int(original.loc[idx, "id"]),
                        "관리번호": 관리번호,
                        "상품코드": new_code,
                    })

            if not changes:
                st.info("변경된 내용이 없습니다.")
            else:
                # 같은 관리번호 중복 편집 시 마지막 값으로 통합
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
                            update_code_single(c["id"], c["상품코드"])

                if needs_confirm:
                    st.session_state.pending_bulk = needs_confirm
                    st.rerun()
                else:
                    st.success(f"✅ {len(auto_apply)}건 저장 완료")
                    st.rerun()
