"""
家族お出かけアプリ — Flask バックエンド
Google Places API + Open-Meteo (無料) + Claude AI
Config: outing_config.json (APIキーはチャットに貼らない)
"""

import json, os, math, requests, anthropic, time as _time_module
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "outing_config.json")

CFG = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CFG = json.load(f)

ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")  or CFG.get("anthropic_key", "")
GOOGLE_KEY      = os.environ.get("GOOGLE_PLACES_KEY")  or CFG.get("google_places_key", "")
MODEL           = CFG.get("model", "claude-haiku-4-5")
PORT            = int(CFG.get("port", 5051))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
app = Flask(__name__, static_folder=BASE_DIR)
JST = timezone(timedelta(hours=9))
SERVER_START = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

# ---------------------------------------------------------------------------
# 気分 → Places 検索クエリ マッピング
# ---------------------------------------------------------------------------
MOOD_QUERIES = {
    "芝生・公園":   ["大きい公園 芝生広場", "総合公園"],
    "遊具":        ["大型遊具 公園 子供", "冒険遊び場"],
    "科学館":      ["科学館 子供", "プラネタリウム"],
    "水族館":      ["水族館"],
    "動物園":      ["動物園 子供"],
    "プール":      ["プール 子供", "水遊び スプラッシュ"],
    "博物館":      ["博物館 子供", "歴史博物館"],
    "アスレチック": ["アスレチック 子供", "冒険の森"],
    "キャンプ・自然":["キャンプ場 子供", "自然体験"],
    "室内遊び場":  ["室内遊び場 子供", "キッズパーク"],
}
DEFAULT_QUERIES = ["子供 遊び場 公園", "ファミリー レジャー施設", "子供 体験施設"]

TRANSPORT_MODES = {
    "car":    "driving",
    "train":  "transit",
    "bike":   "bicycling",
    "walk":   "walking",
}
TRANSPORT_LABELS = {
    "car": "車", "train": "電車・バス", "bike": "自転車", "walk": "徒歩"
}

# ---------------------------------------------------------------------------
# 天気キャッシュ (Open-Meteo レートリミット対策)
# ---------------------------------------------------------------------------
_weather_cache: dict = {}   # (lat2, lng2) -> (timestamp, data)
WEATHER_CACHE_TTL = 600     # 10分間キャッシュ

# ---------------------------------------------------------------------------
# 天気取得 (Open-Meteo — APIキー不要)
# ---------------------------------------------------------------------------
WMO_CODES = {
    0: "快晴", 1: "晴れ", 2: "一部曇り", 3: "曇り",
    45: "霧", 48: "着氷性の霧",
    51: "霧雨（弱）", 53: "霧雨", 55: "霧雨（強）",
    61: "小雨", 63: "雨", 65: "大雨",
    71: "小雪", 73: "雪", 75: "大雪",
    77: "霧雪",
    80: "にわか雨（弱）", 81: "にわか雨", 82: "にわか雨（強）",
    85: "にわか雪", 86: "にわか大雪",
    95: "雷雨", 96: "雹を伴う雷雨", 99: "強雹を伴う雷雨",
}

def get_weather(lat: float, lng: float) -> dict:
    cache_key = (round(lat, 2), round(lng, 2))
    now = _time_module.time()
    if cache_key in _weather_cache:
        ts, cached = _weather_cache[cache_key]
        if now - ts < WEATHER_CACHE_TTL:
            return cached

    try:
        params = {
            "latitude":  lat,
            "longitude": lng,
            "current":   "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "timezone":  "Asia/Tokyo",
            "forecast_days": 1,
        }
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        cur = data.get("current", {})
        if not cur:
            print(f"[WARN] 天気API: currentフィールドなし。レスポンス: {data}")
            return {"temp": 20, "feels_like": 20, "condition": "取得失敗", "wind": 0,
                    "precip": 0, "code": 0, "is_rainy": False, "is_snowy": False,
                    "error": "currentフィールドなし"}
        code = cur.get("weather_code", 0)
        result = {
            "temp":        round(cur.get("temperature_2m", 20), 1),
            "feels_like":  round(cur.get("apparent_temperature", 20), 1),
            "condition":   WMO_CODES.get(code, f"コード{code}"),
            "wind":        round(cur.get("wind_speed_10m", 0), 1),
            "precip":      round(cur.get("precipitation", 0), 1),
            "code":        code,
            "is_rainy":    code in (51,53,55,61,63,65,80,81,82,95,96,99),
            "is_snowy":    code in (71,73,75,77,85,86),
        }
        _weather_cache[cache_key] = (now, result)
        return result
    except requests.exceptions.Timeout:
        print(f"[WARN] 天気取得タイムアウト (lat={lat}, lng={lng})")
        return {"temp": 20, "feels_like": 20, "condition": "取得失敗", "wind": 0,
                "precip": 0, "code": 0, "is_rainy": False, "is_snowy": False,
                "error": "timeout"}
    except Exception as e:
        print(f"[WARN] 天気取得失敗: {type(e).__name__}: {e}")
        return {"temp": 20, "feels_like": 20, "condition": "取得失敗", "wind": 0,
                "precip": 0, "code": 0, "is_rainy": False, "is_snowy": False,
                "error": str(e)}

# ---------------------------------------------------------------------------
# 逆ジオコーディング (Google Geocoding — 同じAPIキーで可)
# ---------------------------------------------------------------------------
def reverse_geocode(lat: float, lng: float) -> str:
    if not GOOGLE_KEY:
        return f"{lat:.3f},{lng:.3f} 付近"
    try:
        url = (
            f"https://maps.googleapis.com/maps/api/geocode/json"
            f"?latlng={lat},{lng}&language=ja&key={GOOGLE_KEY}"
        )
        r = requests.get(url, timeout=6)
        results = r.json().get("results", [])
        for res in results:
            types = res.get("types", [])
            if "locality" in types or "sublocality" in types or "administrative_area_level_2" in types:
                return res.get("formatted_address", "")
        if results:
            return results[0].get("formatted_address", "")
    except Exception as e:
        print(f"[WARN] 逆ジオコーディング失敗: {e}")
    return f"現在地({lat:.3f},{lng:.3f})"

# ---------------------------------------------------------------------------
# Google Places テキスト検索
# ---------------------------------------------------------------------------
def search_places_google(lat: float, lng: float, query: str,
                          radius: int = 30000) -> list[dict]:
    if not GOOGLE_KEY:
        return []
    try:
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            "query":    query,
            "location": f"{lat},{lng}",
            "radius":   radius,
            "language": "ja",
            "key":      GOOGLE_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            print(f"[WARN] Places API: {data.get('status')} — {data.get('error_message','')}")
            return []
        results = []
        for p in data.get("results", [])[:5]:
            loc = p.get("geometry", {}).get("location", {})
            results.append({
                "place_id":   p.get("place_id", ""),
                "name":       p.get("name", ""),
                "address":    p.get("formatted_address", p.get("vicinity", "")),
                "rating":     p.get("rating"),
                "user_ratings_total": p.get("user_ratings_total"),
                "types":      p.get("types", []),
                "lat":        loc.get("lat"),
                "lng":        loc.get("lng"),
                "open_now":   p.get("opening_hours", {}).get("open_now"),
                "photo_ref":  (p.get("photos") or [{}])[0].get("photo_reference"),
            })
        return results
    except Exception as e:
        print(f"[WARN] Places検索失敗 ({query}): {e}")
        return []

# ---------------------------------------------------------------------------
# Google Distance Matrix で移動時間取得
# ---------------------------------------------------------------------------
def get_travel_time(origin_lat, origin_lng, dest_lat, dest_lng,
                    mode: str = "driving") -> dict:
    if not GOOGLE_KEY or dest_lat is None:
        dist_km = haversine(origin_lat, origin_lng, dest_lat or origin_lat,
                            dest_lng or origin_lng)
        speed = {"driving": 50, "transit": 35, "bicycling": 15, "walking": 5}.get(mode, 50)
        mins = round(dist_km / speed * 60)
        return {"text": f"約{mins}分", "value": mins * 60, "estimated": True}
    try:
        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        params = {
            "origins":      f"{origin_lat},{origin_lng}",
            "destinations": f"{dest_lat},{dest_lng}",
            "mode":         mode,
            "language":     "ja",
            "key":          GOOGLE_KEY,
        }
        if mode == "transit":
            params["departure_time"] = int(_time_module.time())
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        rows = r.json().get("rows", [])
        elem = rows[0]["elements"][0] if rows else {}
        status = elem.get("status")
        if status == "OK":
            dur_text = elem["duration"]["text"]
            dur_val  = elem["duration"]["value"]
            return {
                "text":      dur_text,
                "value":     dur_val,
                "distance":  elem["distance"]["text"],
                "estimated": False,
                "mode":      mode,
            }
        # transit が ZERO_RESULTS なら driving で再取得して1.5倍
        if mode == "transit" and status == "ZERO_RESULTS":
            driving_params = {
                "origins":      f"{origin_lat},{origin_lng}",
                "destinations": f"{dest_lat},{dest_lng}",
                "mode":         "driving",
                "language":     "ja",
                "key":          GOOGLE_KEY,
            }
            dr = requests.get(url, params=driving_params, timeout=8)
            dr.raise_for_status()
            dr_rows = dr.json().get("rows", [])
            dr_elem = dr_rows[0]["elements"][0] if dr_rows else {}
            if dr_elem.get("status") == "OK":
                drive_sec = dr_elem["duration"]["value"]
                transit_sec = int(drive_sec * 1.5)
                transit_min = transit_sec // 60
                return {
                    "text":      f"約{transit_min}分",
                    "value":     transit_sec,
                    "distance":  dr_elem["distance"]["text"],
                    "estimated": True,
                    "mode":      "transit_estimated",
                }
    except Exception as e:
        print(f"[WARN] 移動時間取得失敗: {e}")
    # 最終フォールバック（直線距離による概算）
    dist_km = haversine(origin_lat, origin_lng, dest_lat, dest_lng)
    speed = {"driving": 50, "transit": 25, "bicycling": 15, "walking": 5}.get(mode, 50)
    mins = round(dist_km / speed * 60)
    return {"text": f"約{mins}分", "value": mins * 60, "estimated": True, "mode": mode}

def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# ---------------------------------------------------------------------------
# Google Maps 経路URL生成
# ---------------------------------------------------------------------------
def maps_url(origin_lat, origin_lng, dest_lat, dest_lng,
             mode: str = "driving", place_id: str = "") -> str:
    gm_mode = {"driving": "driving", "transit": "transit",
               "bicycling": "bicycling", "walking": "walking"}.get(mode, "driving")
    dest = f"{dest_lat},{dest_lng}"
    return (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={origin_lat},{origin_lng}"
        f"&destination={dest}"
        f"&travelmode={gm_mode}"
    )

# ---------------------------------------------------------------------------
# 写真URL
# ---------------------------------------------------------------------------
def photo_url(photo_ref: str, max_width: int = 400) -> str | None:
    if not photo_ref or not GOOGLE_KEY:
        return None
    return (
        f"https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth={max_width}&photo_reference={photo_ref}&key={GOOGLE_KEY}"
    )

# ---------------------------------------------------------------------------
# Claude AI でスポット理由を生成 / フォールバック提案
# ---------------------------------------------------------------------------
def build_spot_context(weather: dict, transport: str, moods: list[str],
                       location_name: str) -> str:
    mood_str = "・".join(moods) if moods else "特になし"
    transport_label = TRANSPORT_LABELS.get(transport, transport)
    rain_note = "（本日は雨または悪天候）" if weather["is_rainy"] else ""
    return (
        f"現在地: {location_name}\n"
        f"天気: {weather['condition']}{rain_note}  気温: {weather['temp']}℃ "
        f"（体感{weather['feels_like']}℃）  風速: {weather['wind']}m/s\n"
        f"移動手段: {transport_label}\n"
        f"希望テーマ: {mood_str}\n"
        f"同行者: 小学生の子供がいる家族"
    )

def generate_reasons(spots: list[dict], context_str: str) -> list[str]:
    """各スポットに対してClaudeがおすすめ理由を生成する"""
    spots_text = "\n".join(
        f"{i+1}. {s['name']}（{s.get('address','')}）"
        f" 評価:{s.get('rating','不明')} 種別:{','.join(s.get('types',[])[:3])}"
        for i, s in enumerate(spots)
    )
    prompt = f"""あなたは家族お出かけのプロです。以下の状況と候補スポットを踏まえて、
各スポットを小学生の子供がいる家族にすすめる理由を80字以内で生成してください。

【現在の状況】
{context_str}

【候補スポット】
{spots_text}

以下のJSON配列で返してください（スポットの順番通り）:
["理由1", "理由2", "理由3", "理由4", "理由5"]

JSON以外出力しないこと。"""
    try:
        msg = anthropic_client.messages.create(
            model=MODEL, max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0].replace("json","").strip()
        reasons = json.loads(raw)
        return reasons if isinstance(reasons, list) else ["おすすめスポットです"] * len(spots)
    except Exception as e:
        print(f"[WARN] 理由生成失敗: {e}")
        return ["おすすめスポットです"] * len(spots)

def claude_fallback_spots(context_str: str, n: int = 5) -> list[dict]:
    """Google Placesが使えない・結果不足のときClaudeの知識でスポットを補完"""
    prompt = f"""あなたは日本の家族お出かけスポットに詳しいプロです。
以下の状況に合わせて、小学生の子供がいる家族向けのおすすめスポットを{n}件提案してください。
実在する施設のみ挙げてください。

【状況】
{context_str}

以下のJSON配列で返してください:
[
  {{
    "name": "スポット名",
    "address": "住所（都道府県から）",
    "reason": "おすすめ理由（80字以内）",
    "category": "カテゴリ（公園/水族館/動物園/科学館/etc）",
    "estimated_travel_min": 30,
    "note": "注意事項や子供向けポイント（任意）"
  }}
]

JSON以外出力しないこと。"""
    try:
        msg = anthropic_client.messages.create(
            model=MODEL, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0].replace("json","").strip()
        spots = json.loads(raw)
        # フォールバック形式に統一
        for s in spots:
            s["source"] = "ai_fallback"
            s["lat"] = None
            s["lng"] = None
            s["place_id"] = ""
            s["rating"] = None
        return spots
    except Exception as e:
        print(f"[ERROR] Claudeフォールバック失敗: {e}")
        return []

# ---------------------------------------------------------------------------
# メインのスポット収集・整形
# ---------------------------------------------------------------------------
def collect_spots(lat: float, lng: float, transport: str,
                  moods: list[str], weather: dict, location_name: str,
                  max_travel_min: int = 90, extra_query: str = "") -> list[dict]:
    # 検索クエリ決定
    queries = []
    for mood in moods:
        queries.extend(MOOD_QUERIES.get(mood, []))
    if not queries:
        queries = DEFAULT_QUERIES
    if extra_query:
        queries = [f"{q} {extra_query}".strip() for q in queries] or [extra_query]
    queries = list(dict.fromkeys(queries))  # 重複除去

    # Google Places で候補収集
    seen_ids = set()
    candidates = []
    radius = 50000 if transport in ("car", "train") else 20000

    for q in queries:
        results = search_places_google(lat, lng, q, radius)
        for p in results:
            pid = p["place_id"]
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                candidates.append(p)
        if len(candidates) >= 20:
            break

    # 移動時間でフィルタ
    mode = TRANSPORT_MODES.get(transport, "driving")
    context_str = build_spot_context(weather, transport, moods, location_name)
    valid = []

    for c in candidates:
        if c["lat"] is None:
            continue
        travel = get_travel_time(lat, lng, c["lat"], c["lng"], mode)
        travel_min = travel["value"] // 60
        if travel_min > max_travel_min:
            continue
        c["travel"] = travel
        c["travel_min"] = travel_min
        c["maps_url"] = maps_url(lat, lng, c["lat"], c["lng"], mode, c["place_id"])
        c["photo_url"] = photo_url(c.get("photo_ref"))
        valid.append(c)
        if len(valid) >= 10:
            break

    # 評価でソート → 上位5件
    valid.sort(key=lambda x: (-(x.get("rating") or 0), x["travel_min"]))
    top5 = valid[:5]

    # 足りない場合はClaudeで補完
    if len(top5) < 5:
        shortage = 5 - len(top5)
        ai_spots = claude_fallback_spots(context_str, shortage)
        # AI補完スポットには移動時間の概算を付与
        for s in ai_spots:
            s["travel"] = {"text": f"約{s.get('estimated_travel_min',30)}分", "estimated": True}
            s["travel_min"] = s.get("estimated_travel_min", 30)
            s["maps_url"] = f"https://www.google.com/maps/search/?api=1&query={requests.utils.quote(s['name'])}"
            s["photo_url"] = None
        top5.extend(ai_spots)

    # Claude で理由生成（Google Places 取得分のみ）
    google_spots = [s for s in top5 if s.get("source") != "ai_fallback"]
    if google_spots:
        reasons = generate_reasons(google_spots, context_str)
        for i, s in enumerate(google_spots):
            s["reason"] = reasons[i] if i < len(reasons) else "おすすめスポットです"

    return top5[:5]

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/api/version")
def version():
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    return jsonify({
        "ok": True,
        "google_places": bool(GOOGLE_KEY),
        "anthropic": bool(ANTHROPIC_KEY),
        "time": now,
        "deployed_at": SERVER_START,
    })

@app.route("/api/geocode", methods=["GET"])
def geocode_endpoint():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "q が必要です"}), 400
    if not GOOGLE_KEY:
        return jsonify({"error": "Google APIキー未設定"}), 500
    try:
        url = (
            f"https://maps.googleapis.com/maps/api/geocode/json"
            f"?address={requests.utils.quote(query)}&language=ja&region=JP&key={GOOGLE_KEY}"
        )
        r = requests.get(url, timeout=8)
        results = r.json().get("results", [])
        if not results:
            return jsonify({"error": "not found"})
        loc = results[0]["geometry"]["location"]
        name = results[0].get("formatted_address", query)
        return jsonify({"lat": loc["lat"], "lng": loc["lng"], "name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/weather", methods=["GET"])
def weather_endpoint():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if lat is None or lng is None:
        return jsonify({"error": "lat, lng が必要です"}), 400
    return jsonify(get_weather(lat, lng))

@app.route("/api/spots", methods=["POST"])
def spots_endpoint():
    body = request.get_json(force=True)
    lat       = body.get("lat")
    lng       = body.get("lng")
    transport = body.get("transport", "car")
    moods     = body.get("moods", [])
    max_min   = int(body.get("max_travel_min", 90))
    ai_hint   = body.get("ai_hint", "").strip()

    if lat is None or lng is None:
        return jsonify({"error": "lat, lng が必要です"}), 400

    # AI追加指示があればmoods・検索クエリを補完
    extra_query = ""
    if ai_hint:
        try:
            valid_moods = list(MOOD_QUERIES.keys())
            extract = anthropic_client.messages.create(
                model=MODEL, max_tokens=200,
                messages=[{"role": "user", "content":
                    f"お出かけの追加条件:「{ai_hint}」\n"
                    f"現在の気分選択: {moods or '未選択'}\n\n"
                    f"①この条件に合うmoodを以下リストから選ぶ（複数可、なければ[]）:\n"
                    f"  {valid_moods}\n"
                    f"②Google Places検索に使う日本語キーワード（2語以内）:\n\n"
                    f'JSON出力: {{"moods":["室内遊び場","科学館"],"query":"室内 雨天"}}\n'
                    f"JSON以外不要。"
                }]
            )
            raw = extract.content[0].text.strip()
            s, e = raw.find("{"), raw.rfind("}") + 1
            if s >= 0 and e > s:
                parsed = json.loads(raw[s:e])
                ai_moods = [m for m in parsed.get("moods", []) if m in valid_moods]
                # 手動選択moodsにAI推定moodsをマージ
                merged = list(dict.fromkeys(moods + ai_moods))
                if merged:
                    moods = merged
                extra_query = parsed.get("query", "").strip()
            print(f"[AI_HINT] moods={moods}, extra_query={extra_query!r}")
        except Exception as ex:
            print(f"[WARN] ai_hint抽出失敗: {ex}")

    weather       = get_weather(lat, lng)
    location_name = reverse_geocode(lat, lng)
    spots         = collect_spots(lat, lng, transport, moods, weather,
                                  location_name, max_min, extra_query=extra_query)

    return jsonify({
        "spots":         spots,
        "weather":       weather,
        "location_name": location_name,
        "transport":     transport,
        "moods":         moods,
        "generated_at":  datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
    })

@app.route("/api/ai-search", methods=["POST"])
def ai_search_endpoint():
    """自然言語で検索条件を解釈してスポットを返す（上部インプット用）"""
    body    = request.get_json(force=True)
    lat     = body.get("lat")
    lng     = body.get("lng")
    message = body.get("message", "").strip()

    if lat is None or lng is None:
        return jsonify({"error": "位置情報を取得してください"}), 400
    if not message:
        return jsonify({"error": "メッセージを入力してください"}), 400

    # Claude で検索条件を抽出
    valid_moods = list(MOOD_QUERIES.keys())
    valid_transports = list(TRANSPORT_MODES.keys())
    try:
        extract_resp = anthropic_client.messages.create(
            model=MODEL, max_tokens=200,
            messages=[{"role": "user", "content":
                f"ユーザーのお出かけ希望: 「{message}」\n\n"
                f"以下のJSONで検索条件を抽出してください:\n"
                f"moods: {valid_moods} から該当するものを配列で（なければ[]）\n"
                f"transport: {valid_transports} のいずれか（デフォルト: car）\n"
                f"max_travel_min: 移動時間上限の分数（デフォルト: 90）\n"
                f'出力例: {{"moods":["水族館","室内遊び場"],"transport":"car","max_travel_min":60}}\n'
                f"JSON以外出力しないこと。"
            }]
        )
        raw = extract_resp.content[0].text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        params = json.loads(raw[start:end]) if start >= 0 and end > start else {}
    except Exception as e:
        print(f"[WARN] 条件抽出失敗: {e}")
        params = {}

    moods          = [m for m in params.get("moods", []) if m in valid_moods]
    transport      = params.get("transport", "car") if params.get("transport") in valid_transports else "car"
    max_travel_min = int(params.get("max_travel_min", 90))

    weather       = get_weather(lat, lng)
    location_name = reverse_geocode(lat, lng)
    spots         = collect_spots(lat, lng, transport, moods, weather, location_name, max_travel_min)

    return jsonify({
        "spots":         spots,
        "weather":       weather,
        "location_name": location_name,
        "transport":     transport,
        "moods":         moods,
        "max_travel_min": max_travel_min,
        "generated_at":  datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
    })


@app.route("/api/chat", methods=["POST"])
def chat_endpoint():
    """結果画面のフィードバックチャット。返信＋条件を更新してスポット再検索"""
    body    = request.get_json(force=True)
    history = body.get("history", [])
    message = body.get("message", "").strip()
    context = body.get("context", {})
    lat     = body.get("lat")
    lng     = body.get("lng")

    if not message:
        return jsonify({"error": "メッセージを入力してください"}), 400

    spots_text = ""
    for i, s in enumerate(context.get("spots", []), 1):
        spots_text += (
            f"{i}. {s.get('name','')} — "
            f"{s.get('travel',{}).get('text','')} — "
            f"{s.get('reason','')}\n"
        )

    weather = context.get("weather", {})
    system = f"""あなたは家族お出かけのアドバイザーです。
小学生の子供がいる家族が週末の行き先を検討しています。

【現在の提案スポット】
{spots_text or '（なし）'}

【天気・状況】
天気: {weather.get('condition','不明')}  気温: {weather.get('temp','?')}℃
現在地: {context.get('location_name','不明')}
移動手段: {TRANSPORT_LABELS.get(context.get('transport','car'),'車')}
希望テーマ: {'・'.join(context.get('moods',[])) or '特になし'}

フィードバックを受けて、以下のJSONで返してください（他のテキスト不要）:
{{"reply":"アドバイス（100字以内）","moods":["更新後のmoods配列（変更なければ現在のまま）"],"transport":"car/train/bike/walk","max_travel_min":90}}
moods の選択肢: {list(MOOD_QUERIES.keys())}"""

    try:
        resp = anthropic_client.messages.create(
            model=MODEL, max_tokens=400, system=system,
            messages=history + [{"role": "user", "content": message}],
        )
        raw = resp.content[0].text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end]) if start >= 0 and end > start else {}
        reply         = data.get("reply", raw[:100])
        new_moods     = [m for m in data.get("moods", context.get("moods", [])) if m in MOOD_QUERIES]
        new_transport = data.get("transport", context.get("transport", "car"))
        new_max_min   = int(data.get("max_travel_min", context.get("max_travel_min", 90)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # スポット再検索
    new_spots = None
    if lat is not None and lng is not None:
        try:
            new_weather = get_weather(lat, lng)
            location_name = context.get("location_name") or reverse_geocode(lat, lng)
            new_spots = collect_spots(lat, lng, new_transport, new_moods,
                                      new_weather, location_name, new_max_min)
        except Exception as e:
            print(f"[WARN] 再検索失敗: {e}")

    result = {"reply": reply, "transport": new_transport, "moods": new_moods, "max_travel_min": new_max_min}
    if new_spots is not None:
        result["spots"] = new_spots
        result["weather"] = context.get("weather", {})
        result["location_name"] = context.get("location_name", "")
        result["generated_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    return jsonify(result)


@app.route("/api/debug-travel")
def debug_travel():
    """Transit APIのレスポンスを丸ごと返すデバッグ用エンドポイント"""
    # 横浜駅 → 八景島シーパラダイス（電車で実在するルート）
    origin = "35.4658,139.6225"
    dest   = "35.3879,139.6168"
    result = {}
    for mode in ["driving", "transit"]:
        params = {
            "origins":      origin,
            "destinations": dest,
            "mode":         mode,
            "language":     "ja",
            "key":          GOOGLE_KEY,
        }
        if mode == "transit":
            params["departure_time"] = int(_time_module.time())
        try:
            r = requests.get("https://maps.googleapis.com/maps/api/distancematrix/json",
                             params=params, timeout=10)
            result[mode] = r.json()
        except Exception as e:
            result[mode] = {"error": str(e)}
    return jsonify(result)

if __name__ == "__main__":
    print(f"[お出かけアプリ] http://localhost:{PORT} で起動中")
    print(f"  Google Places API: {'✓ 設定済み' if GOOGLE_KEY else '✗ 未設定（AIフォールバックのみ）'}")
    print(f"  Anthropic API:     {'✓ 設定済み' if ANTHROPIC_KEY else '✗ 未設定'}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
