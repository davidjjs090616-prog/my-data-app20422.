"""
어제의 박스오피스 - 스트림릿 앱
------------------------------------
영화진흥위원회(KOBIS) 오픈API를 이용해서 '어제' 일별 박스오피스를 보여줍니다.

배포 전에 꼭 확인하세요:
- Streamlit Cloud의 [App settings] -> [Secrets] 에 아래처럼 인증키를 등록해야 합니다.

    KOBIS_KEY = "여기에_발급받은_인증키"

- 인증키는 코드에 절대 적지 않고, st.secrets를 통해서만 불러옵니다.
"""

import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ------------------------------------------------------------------
# 0) 기본 설정
# ------------------------------------------------------------------
st.set_page_config(page_title="어제의 박스오피스", page_icon="🎬", layout="wide")

KOBIS_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"

# 표에 보여줄 컬럼 이름을 한글로 정리해두는 매핑표입니다.
COLUMN_NAME_MAP = {
    "rank": "순위",
    "rankInten": "순위변동",
    "movieNm": "영화명",
    "openDt": "개봉일",
    "audiCnt": "관객수(당일)",
    "audiAcc": "누적관객",
    "scrnCnt": "스크린수",
}


def get_yesterday_kst() -> str:
    """
    한국 시간(KST) 기준으로 '어제' 날짜를 yyyymmdd 형식 문자열로 돌려줍니다.
    배포 서버의 시계가 한국 시간이 아니어도, ZoneInfo("Asia/Seoul")를 쓰면
    항상 한국 기준 시각으로 계산되므로 안전합니다.
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    yesterday_kst = now_kst - timedelta(days=1)
    return yesterday_kst.strftime("%Y%m%d")


@st.cache_data(ttl=3600, show_spinner="박스오피스 데이터를 불러오는 중...")
def fetch_box_office(target_dt: str):
    """
    KOBIS API를 호출해서 지정한 날짜(target_dt, yyyymmdd)의 박스오피스 목록을 가져옵니다.

    - target_dt 값이 캐시의 '키' 역할을 합니다. 같은 날짜로 다시 호출하면
      1시간(ttl=3600초) 동안은 API를 다시 부르지 않고 저장해둔 결과를 그대로 씁니다.
    - 성공하면 (영화 목록 리스트, None) 을 돌려주고,
      문제가 생기면 (None, "한국어로 된 안내 메시지") 을 돌려줍니다.
    """
    # 1) 비밀 금고에서 인증키 불러오기
    api_key = st.secrets.get("KOBIS_KEY")
    if not api_key:
        return None, (
            "인증키(KOBIS_KEY)를 찾을 수 없습니다. "
            "Streamlit Cloud의 [App settings] → [Secrets]에 KOBIS_KEY 값을 등록했는지 확인해 주세요."
        )

    params = {
        "key": api_key,
        "targetDt": target_dt,
    }

    # 2) 실제 요청 보내기 (네트워크 오류, 타임아웃 등을 대비)
    try:
        response = requests.get(KOBIS_URL, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        return None, f"KOBIS 서버에 연결하지 못했습니다. 인터넷 연결 상태를 확인해 주세요. (오류: {e})"

    # 3) 상태코드 확인 (200이 아니면 서버 쪽 문제일 가능성)
    if response.status_code != 200:
        return None, f"KOBIS 서버가 오류를 반환했습니다. (상태코드: {response.status_code})"

    # 4) JSON 형식이 아닌 응답이 올 수도 있으니 안전하게 파싱
    try:
        data = response.json()
    except ValueError:
        return None, "서버 응답을 해석할 수 없습니다(JSON 형식이 아님). 잠시 후 다시 시도해 주세요."

    # 5) 인증키가 틀렸을 때는 상태코드는 200이지만 faultInfo 상자가 옵니다.
    if "faultInfo" in data:
        fault = data["faultInfo"]
        message = fault.get("message", "알 수 없는 오류")
        return None, (
            f"KOBIS API가 오류를 반환했습니다: {message}\n"
            "인증키(KOBIS_KEY)가 올바른지, 발급 상태가 정상인지 확인해 주세요."
        )

    # 6) 정상 구조인지 확인 (boxOfficeResult > dailyBoxOfficeList)
    box_office_result = data.get("boxOfficeResult")
    if not box_office_result:
        return None, "응답에 boxOfficeResult가 없습니다. KOBIS API 문서와 요청 형식을 다시 확인해 주세요."

    movie_list = box_office_result.get("dailyBoxOfficeList")
    if movie_list is None:
        return None, "응답에 dailyBoxOfficeList가 없습니다. 요청 파라미터(targetDt 등)를 확인해 주세요."

    if len(movie_list) == 0:
        return None, (
            "해당 날짜의 박스오피스 정보가 비어 있습니다. "
            "너무 이른 날짜(집계 전)이거나 날짜 형식(yyyymmdd)을 다시 확인해 주세요."
        )

    return movie_list, None


def to_dataframe(movie_list: list) -> pd.DataFrame:
    """
    API에서 받은 영화 목록(문자열 숫자 포함)을 pandas DataFrame으로 바꾸고,
    숫자로 와야 할 컬럼들을 실제 숫자(int) 타입으로 변환합니다.
    이렇게 해야 정렬(sort)이나 그래프에서 문자열이 아니라 크기 순서대로 계산됩니다.
    """
    df = pd.DataFrame(movie_list)

    # 숫자로 바꿔야 하는 컬럼 목록
    numeric_columns = ["rank", "audiCnt", "audiAcc", "scrnCnt", "showCnt"]
    for col in numeric_columns:
        if col in df.columns:
            # errors="coerce": 혹시 이상한 값이 있어도 앱이 죽지 않고 NaN으로 처리됩니다.
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 순위(rank) 기준으로 정렬
    df = df.sort_values("rank").reset_index(drop=True)
    return df


def format_number(n) -> str:
    """숫자를 1,000 단위 콤마가 있는 문자열로 보기 좋게 바꿔줍니다."""
    if pd.isna(n):
        return "-"
    return f"{int(n):,}"


# ------------------------------------------------------------------
# 1) 화면 그리기 시작
# ------------------------------------------------------------------
st.title("🎬 어제의 박스오피스")

target_dt = get_yesterday_kst()
target_dt_display = f"{target_dt[0:4]}.{target_dt[4:6]}.{target_dt[6:8]}"
st.caption(f"기준 날짜(한국시간 기준 어제): {target_dt_display}")

movie_list, error_message = fetch_box_office(target_dt)

# 오류가 있으면 안내 메시지를 보여주고 여기서 멈춥니다 (빈 화면 방지).
if error_message:
    st.error(error_message)
    st.stop()

df = to_dataframe(movie_list)

# ------------------------------------------------------------------
# 2) 1위 영화 - 지표 카드 3장
# ------------------------------------------------------------------
top1 = df.iloc[0]

st.subheader(f"🥇 1위: {top1['movieNm']}")

col1, col2, col3 = st.columns(3)
col1.metric("당일 관객수", format_number(top1["audiCnt"]))
col2.metric("누적 관객수", format_number(top1["audiAcc"]))
col3.metric("스크린수", format_number(top1["scrnCnt"]))

st.divider()

# ------------------------------------------------------------------
# 3) 관객수 상위 5편 - 막대그래프
# ------------------------------------------------------------------
st.subheader("📊 관객수 상위 5편")

top5 = df.sort_values("audiCnt", ascending=False).head(5)
chart_data = top5.set_index("movieNm")[["audiCnt"]]
chart_data = chart_data.rename(columns={"audiCnt": "당일 관객수"})
st.bar_chart(chart_data)

st.divider()

# ------------------------------------------------------------------
# 4) 전체 순위표
# ------------------------------------------------------------------
st.subheader("📋 전체 순위")

table_columns = ["rank", "rankInten", "movieNm", "openDt", "audiCnt", "audiAcc", "scrnCnt"]
table_columns = [c for c in table_columns if c in df.columns]  # 혹시 없는 컬럼 방어

display_df = df[table_columns].copy()
display_df = display_df.rename(columns=COLUMN_NAME_MAP)

# 보기 좋게 숫자에 콤마 넣기 (표시용 문자열로만 바꾸고, 정렬은 이미 위에서 숫자로 끝냈습니다)
for col_key, col_name in COLUMN_NAME_MAP.items():
    if col_name in display_df.columns and col_key in ("audiCnt", "audiAcc", "scrnCnt"):
        display_df[col_name] = display_df[col_name].apply(format_number)

st.dataframe(display_df, use_container_width=True, hide_index=True)
