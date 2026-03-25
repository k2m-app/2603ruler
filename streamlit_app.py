import streamlit as st
import streamlit.components.v1 as components
import re
import sys
import os

# app.py から統合版のロジック部分のみインポート
sys.path.insert(0, os.path.dirname(__file__))
from app import (
    NetkeibaScraper,
    build_unified_graph,
    analyze_all_horses_html,
)

# ==========================================
# CSS
# ==========================================
PAGE_STYLE = """
<style>
  body { font-family: 'Helvetica Neue', Arial, sans-serif; background: #f7f6f2; margin: 0; padding: 10px; color: #333; }
  .section-title { background-color: #2c3e50; color: white; padding: 10px 15px; border-radius: 6px;
                   font-size: 15px; margin-top: 20px; margin-bottom: 15px; }
  .horse-rank { margin-bottom: 18px; padding-bottom: 10px; border-bottom: 1px dashed #ccc; }
  .rank-title { font-size: 15px; margin: 0 0 5px 0; color: #2c3e50; }
  .time-diff { color: #e74c3c; font-weight: bold; }
  .theory-box { background-color: #f8f9fa; border-left: 4px solid #7b8d7a; padding: 10px 12px;
                border-radius: 0 6px 6px 0; font-size: 13px; line-height: 1.6; }
  .race-link { color: #3498db; text-decoration: none; font-weight: bold; }
  .race-link:hover { color: #2980b9; text-decoration: underline; }
  .ranking-list { margin-bottom: 10px; }
</style>
"""

def wrap_html(content_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {PAGE_STYLE}
</head>
<body>
  {content_html}
</body>
</html>"""

def run_analysis(race_id: str, water_mode: str | None = None):
    scraper = NetkeibaScraper()
    race_title, target_course, target_track, target_distance, past_races, umaban_dict = \
        scraper.fetch_past5_data(race_id, water_mode=water_mode)

    is_banei = (target_track == "ばんえい")

    G_unified = build_unified_graph(
        past_races, target_course, target_track, target_distance, umaban_dict
    )
    
    result_html_content, _, _, _, _ = analyze_all_horses_html(
        G_unified, umaban_dict, target_course, target_distance, race_id=None, is_banei=is_banei
    )

    water_note = ""
    if is_banei and water_mode:
        label = "1.9%以下（軽馬場）" if water_mode == "dry" else "2.0%以上（重馬場）"
        water_note = f"<p style='color:#2980b9; font-size:13px;'>💧 水分量フィルタ: <strong>{label}</strong></p>"
        
    result_html = (
        f"{water_note}"
        f"<h2 class='section-title'>📊 {target_course} {target_distance}m 基準：能力序列<br>"
        f"<span style='font-size:0.75em; font-weight:normal; color:#bdc3c7;'>※同条件の直接対決を絶対視し、間接比較には0.7倍のノイズ割引を適用</span></h2>"
        f"{result_html_content}"
    )

    return race_title, result_html

# ==========================================
# Streamlit UI
# ==========================================
st.set_page_config(page_title="競馬物差しツール", page_icon="🏇", layout="centered")
st.title("🏇 競馬物差しツール")
st.caption("netkeiba のレースURLを入力すると、全出走馬の総当たり比較（拡張馬柱）を生成します。")

with st.form("race_form"):
    url_input = st.text_input(
        "netkeibaのレースURL",
        placeholder="https://race.netkeiba.com/race/result.html?race_id=202306050811",
    )

    col1, col2 = st.columns(2)
    with col1:
        race_nums = st.text_input(
            "複数レース（任意）",
            placeholder="例: 1,3,5,11",
        )
    with col2:
        water_mode = st.selectbox(
            "水分量フィルタ（ばんえい専用）",
            options=["なし", "軽馬場（dry）", "重馬場（wet）"],
        )

    submitted = st.form_submit_button("全頭比較を開始", type="primary", use_container_width=True)

if submitted:
    scraper = NetkeibaScraper()
    base_race_id = scraper.extract_race_id(url_input)

    if not base_race_id:
        st.error("正しいnetkeibaのURLを入力してください（12桁のrace_idが含まれるもの）")
        st.stop()

    race_ids = [base_race_id]
    if race_nums.strip():
        base_prefix = base_race_id[:10]
        for r in re.split(r"[,\s]+", race_nums.strip()):
            if r.isdigit():
                rid = base_prefix + str(int(r)).zfill(2)
                if rid != base_race_id:
                    race_ids.append(rid)
        race_ids = sorted(set(race_ids), key=lambda x: int(x[-2:]))

    wmode = None
    if water_mode == "軽馬場（dry）":
        wmode = "dry"
    elif water_mode == "重馬場（wet）":
        wmode = "wet"

    if len(race_ids) == 1:
        with st.spinner("出走馬の全頭総当たり比較を計算中..."):
            try:
                race_title, result_html = run_analysis(race_ids[0], wmode)
                st.subheader(race_title)
                full_html = wrap_html(result_html)
                components.html(full_html, height=3000, scrolling=True)
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
    else:
        tabs = st.tabs([f"{int(rid[-2:])}R" for rid in race_ids])
        for tab, rid in zip(tabs, race_ids):
            with tab:
                with st.spinner(f"{int(rid[-2:])}R を計算中..."):
                    try:
                        race_title, result_html = run_analysis(rid, wmode)
                        st.subheader(race_title)
                        full_html = wrap_html(result_html)
                        components.html(full_html, height=3000, scrolling=True)
                    except Exception as e:
                        st.error(f"エラー: {e}")
