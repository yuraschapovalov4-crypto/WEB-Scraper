import re
import time
import requests
import pandas as pd
import xml.etree.ElementTree as ET
import streamlit as st
from io import BytesIO

st.set_page_config(page_title="App Store Reviews Parser", layout="wide")

COUNTRY_CODES = [
    "us","gb","ru","de","fr","it","es","nl","pl","tr","ua","kz","jp","kr","cn",
    "ca","au","br","mx","se","no","dk","fi","pt","gr","cz","hu","ro","sk","at",
    "ch","be","ie","nz","sg","hk","in","id","my","th","vn","ph","za","ae","sa",
    "il","cl","ar","co","pe"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "im": "http://itunes.apple.com/rss"
}


def extract_app_id(url: str) -> str:
    match = re.search(r"/id(\d+)", url)
    if not match:
        raise ValueError("Не удалось извлечь app_id из ссылки.")
    return match.group(1)


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def fetch_one_page(app_id: str, country: str, page: int):
    urls = [
        f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/xml",
        f"https://itunes.apple.com/{country}/rss/customerreviews/id={app_id}/page={page}/sortby=mostrecent/xml",
    ]

    xml_text = None
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200 and r.text.strip():
                xml_text = r.text
                break
        except Exception:
            pass

    if not xml_text:
        return []

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    entries = root.findall("atom:entry", NS)
    if not entries:
        return []

    rows = []

    for idx, entry in enumerate(entries):
        rating = safe_text(entry.findtext("im:rating", default="", namespaces=NS))

        # Первая entry может быть метаданными приложения
        if not rating:
            continue

        review_id = safe_text(entry.findtext("atom:id", default="", namespaces=NS))
        author = safe_text(entry.findtext("atom:author/atom:name", default="", namespaces=NS))
        title = safe_text(entry.findtext("atom:title", default="", namespaces=NS))
        content = safe_text(entry.findtext("atom:content", default="", namespaces=NS))
        version = safe_text(entry.findtext("im:version", default="", namespaces=NS))
        updated = safe_text(entry.findtext("atom:updated", default="", namespaces=NS))

        review_text = content
        if title and content and title.lower() != content.lower():
            review_text = f"{title}. {content}"
        elif title and not content:
            review_text = title

        if not review_id:
            review_id = f"{country}_{page}_{idx}_{author}_{updated}"

        rows.append({
            "review_id": review_id,
            "author": author,
            "review": review_text,
            "rating": pd.to_numeric(rating, errors="coerce"),
            "version": version,
            "date_time": updated,
            "country": country
        })

    return rows


def fetch_reviews_for_country(app_id: str, country: str, max_pages: int = 150, sleep_sec: float = 0.15):
    country_rows = []
    seen_ids = set()
    empty_streak = 0

    for page in range(1, max_pages + 1):
        rows = fetch_one_page(app_id, country, page)

        if not rows:
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue

        empty_streak = 0

        new_count = 0
        for row in rows:
            if row["review_id"] in seen_ids:
                continue
            seen_ids.add(row["review_id"])
            country_rows.append(row)
            new_count += 1

        if new_count == 0:
            break

        time.sleep(sleep_sec)

    return country_rows


def collect_last_reviews(app_url: str, last_n: int = 100):
    app_id = extract_app_id(app_url)
    all_rows = []

    for country in COUNTRY_CODES:
        rows = fetch_reviews_for_country(app_id, country, max_pages=150)
        all_rows.extend(rows)

    if not all_rows:
        raise ValueError("Не удалось собрать отзывы.")

    df = pd.DataFrame(all_rows)

    df = df.drop_duplicates(subset=["review_id"], keep="first").copy()
    df["date_time"] = pd.to_datetime(df["date_time"], errors="coerce", utc=True).dt.tz_localize(None)

    df = df.sort_values("date_time", ascending=False).reset_index(drop=True)
    df = df.head(last_n).copy()

    df = df[["author", "review", "rating", "version", "date_time", "country"]]
    return df


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm:ss") as writer:
        df.to_excel(writer, sheet_name="reviews", index=False)

        workbook = writer.book
        worksheet = writer.sheets["reviews"]

        worksheet.set_column("A:A", 25)
        worksheet.set_column("B:B", 100)
        worksheet.set_column("C:C", 10)
        worksheet.set_column("D:D", 15)
        worksheet.set_column("E:E", 22)
        worksheet.set_column("F:F", 10)

        wrap_format = workbook.add_format({"text_wrap": True, "valign": "top"})
        worksheet.set_column("B:B", 100, wrap_format)
        worksheet.set_default_row(60)

    output.seek(0)
    return output.getvalue()


st.title("App Store Reviews Parser")
st.write("Сбор последних 100 отзывов по ссылке на приложение из App Store.")

app_url = st.text_input(
    "Ссылка на приложение App Store",
    value="https://apps.apple.com/ru/app/subway-surfers-city/id6504188939"
)

if st.button("Собрать отзывы"):
    if not app_url.strip():
        st.error("Вставь ссылку на приложение.")
    else:
        try:
            with st.spinner("Собираю отзывы..."):
                df_reviews = collect_last_reviews(app_url, last_n=100)

            st.success(f"Собрано отзывов: {len(df_reviews)}")
            st.dataframe(df_reviews, use_container_width=True)

            excel_data = dataframe_to_excel_bytes(df_reviews)

            st.download_button(
                label="Скачать Excel",
                data=excel_data,
                file_name="appstore_last_100_reviews.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        except Exception as e:
            st.error(f"Ошибка: {e}")
