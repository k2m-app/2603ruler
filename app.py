import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
import networkx as nx

# ==========================================
# 1. ユーティリティ・条件判定関数
# ==========================================
def parse_date(date_str):
    try:
        if '.' in date_str:
            parts = date_str.split('.')
            yy = int(parts[0])
            if yy < 100: yy += 2000
            return datetime(yy, int(parts[1]), int(parts[2]))
        elif '/' in date_str:
            parts = date_str.split('/')
            yy = int(parts[0])
            if yy < 100: yy += 2000
            return datetime(yy, int(parts[1]), int(parts[2]))
    except:
        pass
    return None

def is_older_than_1_year(r_date):
    if not r_date: return False
    now = datetime.now()
    return (now - r_date).days > 365

def get_ooi_layout(dist):
    try:
        d = int(dist)
        return "inner" if d in [1500, 1600, 1650] else "outer"
    except:
        return "outer"

def determine_condition(t_place, t_dist, r_place, r_dist):
    """ 条件A(同条件), 条件B(同競馬場同形態・異距離), 条件C(他競馬場または異形態) を判定 """
    if t_place == r_place and str(t_dist) == str(r_dist):
        return 'A'
    
    if t_place == r_place:
        if t_place == "大井":
            if get_ooi_layout(t_dist) == get_ooi_layout(r_dist):
                return 'B'
            else:
                return 'C' # 大井の内・外違いは条件C
        return 'B'
    
    return 'C'

def calculate_matchup_points(diff, cond, is_banei=False):
    """ 着差と条件から基礎ポイントを算出 (負けの場合はマイナス) """
    abs_diff = abs(diff)
    pts = 0.0
    
    if is_banei:
        if abs_diff >= 4.0: pts = 3.0
        elif abs_diff >= 1.5: pts = 1.0
    else:
        if cond == 'A':
            if abs_diff >= 1.1: pts = 3.0
            elif abs_diff >= 0.6: pts = 1.0
        elif cond == 'B':
            if abs_diff >= 1.3: pts = 3.0
            elif abs_diff >= 0.8: pts = 1.0
        elif cond == 'C':
            if abs_diff >= 1.5: pts = 3.0
            elif abs_diff >= 1.0: pts = 1.0

    # diff < 0 は自馬の先着(勝ち)
    return pts if diff <= 0 else -pts

# ==========================================
# 2. Netkeiba ディープスクレイパー
# ==========================================
class NetkeibaScraper:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0'}

    def extract_race_id(self, url):
        match = re.search(r'\d{12}', url)
        return match.group(0) if match else None

    def convert_time_to_sec(self, time_str):
        try:
            if ':' in time_str:
                m, s = time_str.split(':')
                return int(m) * 60 + float(s)
            return float(time_str)
        except:
            return None

    def fetch_past5_data(self, race_id, water_mode=None):
        is_nar = int(race_id[4:6]) > 10 if len(race_id) >= 6 else False
        url_domain = "nar.netkeiba.com" if is_nar else "race.netkeiba.com"
        url = f"https://{url_domain}/race/shutuba_past.html?race_id={race_id}"
        time.sleep(1)
        res = requests.get(url, headers=self.headers)
        res.encoding = 'EUC-JP'
        soup = BeautifulSoup(res.text, 'html.parser')

        title_tag = soup.find(class_='RaceName')
        race_title = title_tag.text.strip() if title_tag else f"レースID: {race_id}"

        race_data01 = soup.find('div', class_='RaceData01')
        race_data02 = soup.find('div', class_='RaceData02')

        is_banei = race_data02 is not None and '(ば)' in race_data02.text

        target_course, target_distance = "不明", "不明"
        if race_data02:
            c_match = re.search(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)', race_data02.text)
            if c_match: target_course = c_match.group(1)
        if race_data01:
            d_match = re.search(r'(\d{3,4})', race_data01.text)
            if d_match: target_distance = d_match.group(1)

        past_races_dict = {}
        umaban_dict = {}
        deep_dive_candidates = set()

        for tr in soup.find_all('tr', class_='HorseList'):
            name_tag = tr.find(class_='Horse02')
            if not name_tag: continue
            horse_name = name_tag.find('a').text.strip()

            tds = tr.find_all('td')
            if len(tds) > 1:
                umaban_dict[horse_name] = tds[1].text.strip()

            past_tds = tr.find_all('td', class_=re.compile(r'^Past'))
            for i, td in enumerate(past_tds):
                data01 = td.find('div', class_='Data01')
                data02_a = td.find('div', class_='Data02').find('a') if td.find('div', class_='Data02') else None
                data05 = td.find('div', class_='Data05')

                if data01 and data02_a and data05:
                    past_race_id = self.extract_race_id(data02_a['href'])
                    c_match = re.search(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)', data01.text)
                    course = c_match.group(1) if c_match else "不明"
                    
                    date_match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', data01.text)
                    r_date_str = f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}" if date_match else ""
                    
                    d_match = re.search(r'(\d{3,4})', data05.text)
                    distance = d_match.group(1) if d_match else "不明"

                    if past_race_id not in past_races_dict:
                        past_races_dict[past_race_id] = {
                            'race_id': past_race_id, 'date_str': r_date_str, 'date': parse_date(r_date_str),
                            'course': course, 'distance': distance, 'horses': {}
                        }

                    # 同条件なら無制限、異条件なら過去3走まで深掘り対象
                    if course == target_course and str(distance) == str(target_distance):
                        deep_dive_candidates.add(past_race_id)
                    elif i < 3:
                        deep_dive_candidates.add(past_race_id)

        for past_id in deep_dive_candidates:
            time.sleep(0.3)
            db_url = f"https://db.netkeiba.com/race/{past_id}/"
            res = requests.get(db_url, headers=self.headers)
            res.encoding = 'EUC-JP'
            db_soup = BeautifulSoup(res.text, 'html.parser')
            result_table = db_soup.find('table', class_='race_table_01')

            if not result_table: continue

            winner_sec = None
            for tr in result_table.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) < 7: continue

                rank_str = tds[0].text.strip()
                if not rank_str.isdigit(): continue

                horse_cell = tds[3]
                horse_link = horse_cell.find('a')
                h_name = horse_link.text.strip() if horse_link else horse_cell.text.strip()

                time_str = None
                if len(tds) > 7: time_str = tds[7].text.strip()
                if not self.convert_time_to_sec(time_str):
                    for td in tds[4:]:
                        txt = td.text.strip()
                        if re.match(r'^\d{1,2}:\d{2}\.\d$', txt):
                            time_str = txt
                            break
                if not time_str: continue

                sec = self.convert_time_to_sec(time_str)
                if not sec: continue

                if winner_sec is None: winner_sec = sec
                time_behind = round(sec - winner_sec, 1)

                if past_id in past_races_dict:
                    past_races_dict[past_id]['horses'][h_name] = time_behind

        return race_title, target_course, target_distance, list(past_races_dict.values()), umaban_dict, is_banei

# ==========================================
# 3. 勝負づけ判定ロジック（ポイント制グラフ）
# ==========================================
def build_matchup_graph(past_races, target_course, target_distance, umaban_dict, is_banei):
    G = nx.DiGraph()
    runners = list(umaban_dict.keys())
    
    # 全馬をノードとして追加
    for r in runners:
        G.add_node(r)

    # 過去レース履歴の整理
    match_history = {u: {v: [] for v in runners} for u in runners}

    for race in past_races:
        r_course = race['course']
        r_dist = race['distance']
        r_date = race['date']
        r_date_str = race['date_str']
        
        cond = determine_condition(target_course, target_distance, r_course, r_dist)
        
        horses_in_race = [h for h in race['horses'].items() if h[0] in umaban_dict or h[1] < 2.0]
        
        for i in range(len(horses_in_race)):
            for j in range(i + 1, len(horses_in_race)):
                h1, t1 = horses_in_race[i]
                h2, t2 = horses_in_race[j]
                
                diff = t1 - t2 # H1が早い場合 diff < 0
                pts_h1 = calculate_matchup_points(diff, cond, is_banei)
                
                hist_entry = {
                    'date': r_date, 'date_str': r_date_str, 'course': r_course, 'distance': r_dist,
                    'cond': cond, 'raw_diff': diff, 'pts_h1': pts_h1
                }
                
                if h1 in runners and h2 in runners:
                    match_history[h1][h2].append(hist_entry)
                    match_history[h2][h1].append({**hist_entry, 'raw_diff': -diff, 'pts_h1': -pts_h1})
                elif h1 in runners and h2 not in runners:
                    if not G.has_edge(h1, h2): G.add_edge(h1, h2, history=[])
                    G[h1][h2]['history'].append(hist_entry)
                elif h2 in runners and h1 not in runners:
                    if not G.has_edge(h2, h1): G.add_edge(h2, h1, history=[])
                    G[h2][h1]['history'].append({**hist_entry, 'raw_diff': -diff, 'pts_h1': -pts_h1})

    # 直接対決の勝負づけ完了ポイント計算
    for u in runners:
        for v in runners:
            if u == v or not match_history[u][v]: continue
            
            history = sorted(match_history[u][v], key=lambda x: x['date'] or datetime.min, reverse=True)
            total_score = 0.0
            
            for idx, h in enumerate(history):
                mult = 1.0
                if idx == 0: mult *= 1.5 # 前走（直近）ボーナス
                if is_older_than_1_year(h['date']): mult *= 0.7 # 1年以上前減衰
                
                total_score += h['pts_h1'] * mult
                
            G.add_edge(u, v, score=total_score, history=history, is_direct=True)

    # 隠れ馬経由の間接スコア計算（割引あり）
    for u in runners:
        for v in runners:
            if u == v or G.has_edge(u, v): continue
            
            indirect_scores = []
            for hidden in G.nodes():
                if hidden in runners: continue
                if G.has_edge(u, hidden) and G.has_edge(hidden, v):
                    # 隠れ馬に対するポイントを合算（ただし不確実性のため0.5倍）
                    u_to_h = sum(h['pts_h1'] for h in G[u][hidden]['history'])
                    h_to_v = sum(h['pts_h1'] for h in G[hidden][v]['history'])
                    implied_score = (u_to_h + h_to_v) * 0.5
                    indirect_scores.append(implied_score)
            
            if indirect_scores:
                avg_indirect = sum(indirect_scores) / len(indirect_scores)
                G.add_edge(u, v, score=avg_indirect, history=[], is_direct=False)

    return G

# ==========================================
# 4. リーグ戦評価 ＆ 👑無敗ボーナス
# ==========================================
def evaluate_league(G, umaban_dict):
    runners = list(umaban_dict.keys())
    matchup_results = {u: {} for u in runners}
    
    # 対戦関係の決定
    for u in runners:
        for v in runners:
            if u == v or not G.has_edge(u, v): continue
            
            score = G[u][v]['score']
            if score >= 4.0: rel = ">>"
            elif score >= 1.0: rel = ">"
            elif score > -1.0: rel = "="
            elif score <= -4.0: rel = "<<"
            else: rel = "<"
            
            matchup_results[u][v] = {'rel': rel, 'score': score, 'is_direct': G[u][v]['is_direct']}

    # リーグ戦ポイント集計
    league_scores = {u: 0.0 for u in runners}
    opponents_count = {u: 0 for u in runners}
    undefeated_flags = {u: True for u in runners}

    for u in runners:
        for v, data in matchup_results[u].items():
            opponents_count[u] += 1
            rel = data['rel']
            
            # リーグ基本点
            if rel == ">>": pts = 3.0
            elif rel == ">": pts = 1.0
            elif rel == "=": pts = 0.2  # 「負けない」ことへの微小評価
            elif rel == "<": pts = -1.5
            elif rel == "<<": pts = -3.0
            
            # 隠れ馬経由は影響を少し下げる
            if not data['is_direct']: pts *= 0.7
            league_scores[u] += pts
            
            if rel in ["<", "<<"]:
                undefeated_flags[u] = False

    # 平均化 ＋ 無敗ボーナス適用
    final_scores = {}
    for u in runners:
        if opponents_count[u] == 0:
            final_scores[u] = -999.0 # 測定不能
            continue
            
        avg = league_scores[u] / opponents_count[u]
        
        # 👑 無敗の帝王ボーナス (誰にも負けておらず、比較対象が複数いる場合)
        has_bonus = undefeated_flags[u] and opponents_count[u] >= 1
        if has_bonus:
            avg += 2.5 # SランクやA上位に押し上げる強烈なボーナス
            
        final_scores[u] = avg

    # ティア分け
    ranked = sorted([(u, s) for u, s in final_scores.items() if s != -999.0], key=lambda x: x[1], reverse=True)
    tier_map = {}
    
    if ranked:
        top_s = ranked[0][1]
        for u, s in ranked:
            if s >= 1.5 and (top_s - s) <= 1.0: tier_map[u] = "S"
            elif s >= 0.5: tier_map[u] = "A"
            elif s >= -1.0: tier_map[u] = "B"
            else: tier_map[u] = "C"

    unranked = [u for u in runners if final_scores[u] == -999.0]
    
    return tier_map, matchup_results, ranked, unranked, undefeated_flags

# ==========================================
# 5. HTMLレンダリング
# ==========================================
def build_html_output(tier_map, matchup_results, ranked, unranked, undefeated_flags, umaban_dict, G):
    html = ["<div style='font-family: sans-serif; font-size:14px; color:#333;'>"]
    tier_colors = {"S": "#e74c3c", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    
    for tier in ["S", "A", "B", "C"]:
        horses_in_tier = [u for u, s in ranked if tier_map.get(u) == tier]
        if not horses_in_tier: continue
        
        html.append(f"<h3 style='background-color:{tier_colors[tier]}; color:white; padding:8px; border-radius:4px;'>🏆 {tier}ランク</h3>")
        
        for u in horses_in_tier:
            uma = umaban_dict.get(u, "?")
            bonus_badge = " <span style='background:#f1c40f; color:#fff; padding:2px 6px; border-radius:12px; font-size:0.8em;'>👑 無敗ボーナス適用</span>" if undefeated_flags[u] else ""
            html.append(f"<div style='margin-bottom: 15px; border-left: 4px solid {tier_colors[tier]}; padding-left: 10px;'>")
            html.append(f"  <strong style='font-size:1.1em;'>[{uma}] {u}</strong> {bonus_badge}")
            html.append("  <ul style='margin-top:5px; padding-left:20px; font-size:0.9em;'>")
            
            # 対戦履歴の詳細表示
            for v, data in matchup_results[u].items():
                rel = data['rel']
                v_uma = umaban_dict.get(v, "?")
                is_dir = data['is_direct']
                
                if rel in [">>", ">"]: color, sym = "#27ae60", rel
                elif rel == "=": color, sym = "#888", "＝"
                else: color, sym = "#e74c3c", rel
                
                dir_badge = "" if is_dir else "<span style='color:#9b59b6; font-size:0.8em;'>(隠れ馬経由)</span> "
                
                # 詳細な理由（履歴）
                reasons = []
                if is_dir and u in G and v in G[u]:
                    for h in G[u][v]['history']:
                        c_badge = f"[{h['cond']}]"
                        sign = "+" if h['pts_h1'] > 0 else ""
                        reasons.append(f"{h['date_str']} {h['course']}{h['distance']} {c_badge}({sign}{h['pts_h1']}Pt)")
                
                reason_str = f" <span style='color:#666; font-size:0.85em;'>... {', '.join(reasons)}</span>" if reasons else ""
                
                html.append(f"<li><span style='color:{color}; font-weight:bold;'>{sym}</span> [{v_uma}] {v} {dir_badge}{reason_str}</li>")
            
            html.append("  </ul></div>")

    if unranked:
        html.append("<h3 style='background-color:#95a5a6; color:white; padding:8px; border-radius:4px;'>❗ 測定不能（別路線）</h3>")
        html.append(f"<p>{'、'.join([f'[{umaban_dict.get(u, '?')}] {u}' for u in unranked])}</p>")

    html.append("</div>")
    return "".join(html)

def wrap_combined_html(results_list):
    tabs, contents = "", ""
    for i, (r_num, r_title, content) in enumerate(results_list):
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab-btn {active}" onclick="openTab(event, \'race_{r_num}\')">{r_num}R</button>\n'
        contents += f'<div id="race_{r_num}" class="tab-content {active}"><h2 class="race-title">📊 {r_title}</h2>{content}</div>'

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><style>
    body {{ font-family: sans-serif; background: #f7f6f2; padding: 20px; }}
    .container {{ background: #fff; padding: 20px; border-radius: 8px; max-width: 900px; margin: auto; }}
    .tab-buttons {{ display: flex; gap: 5px; border-bottom: 2px solid #3498db; margin-bottom: 20px; }}
    .tab-btn {{ padding: 10px 16px; border: none; background: #ecf0f1; cursor: pointer; }}
    .tab-btn.active {{ background: #3498db; color: white; }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
</style></head><body>
<div class="container"><div class="tab-buttons">{tabs}</div>{contents}</div>
<script>
function openTab(evt, id) {{
    document.querySelectorAll('.tab-content, .tab-btn').forEach(e => e.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    evt.currentTarget.classList.add('active');
}}
</script></body></html>"""

# ==========================================
# 6. Streamlit UI
# ==========================================
st.set_page_config(page_title="競馬AI 究極相対評価", page_icon="🏇")

st.title("🏇 競馬AI 究極相対評価 (ポイント勝負づけ版)")
st.caption("全馬との対戦をポイント化し、「負けない馬（無敗ボーナス）」を的確にピックアップするプロ馬券師仕様のAIです。")

url_input = st.text_input("netkeibaのレースURL", placeholder="https://race.netkeiba.com/race/result.html?race_id=202405020111")
water_mode = st.selectbox("水分量フィルタ（ばんえい専用）", ["なし", "軽馬場（dry）", "重馬場（wet）"])

st.markdown("---")
selected_races = [i for i in range(1, 13) if st.checkbox(f"{i}R", key=f"chk_{i}")]
submitted = st.button("🚀 究極分析を開始", type="primary")

if submitted and url_input:
    scraper = NetkeibaScraper()
    base_id = scraper.extract_race_id(url_input)
    if not base_id: st.stop()
    if not selected_races: selected_races = [int(base_id[-2:])]

    wmode = "dry" if "軽" in water_mode else "wet" if "重" in water_mode else None
    results = []
    
    progress = st.progress(0)
    for idx, r in enumerate(selected_races):
        rid = f"{base_id[:10]}{r:02d}"
        
        try:
            r_title, t_course, t_dist, past_races, uma_dict, is_banei = scraper.fetch_past5_data(rid, wmode)
            if not uma_dict:
                results.append((r, f"{r}R (出走なし)", "データなし"))
                continue
                
            G = build_matchup_graph(past_races, t_course, t_dist, uma_dict, is_banei)
            tier_map, matchups, ranked, unranked, undef_flags = evaluate_league(G, uma_dict)
            html_out = build_html_output(tier_map, matchups, ranked, unranked, undef_flags, uma_dict, G)
            
            results.append((r, r_title, html_out))
        except Exception as e:
            results.append((r, f"{r}R (エラー)", str(e)))
            
        progress.progress((idx + 1) / len(selected_races))

    st.success("✅ 分析完了！")
    st.download_button("📥 HTML一括ダウンロード", wrap_combined_html(results), file_name=f"究極評価_{base_id[:10]}.html", mime="text/html")
    
    tabs = st.tabs([f"{r[0]}R" for r in results])
    for tab, (r_num, r_title, r_html) in zip(tabs, results):
        with tab:
            st.markdown(r_html, unsafe_allow_html=True)
