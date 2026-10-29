"""会員制バー顧客管理デモの通し試験（ログイン〜来店受付〜年会費〜会計〜会員〜ボトル〜売上レポート〜設定〜権限〜スマホ幅）。
使い方: python3 e2e_test.py [URL]   既定はローカルの index.html（今日を 2026-10-29 に固定）
バーコードの読み取り確認に zxing-cpp を使う（.testlib に展開した物。無ければその項目だけ飛ばさず NG にする）
"""
import io
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / ".testlib"))
from playwright.sync_api import sync_playwright  # noqa: E402

try:
    import zxingcpp  # noqa: E402
    from PIL import Image  # noqa: E402
except Exception:  # pragma: no cover
    zxingcpp = None

BASE = sys.argv[1] if len(sys.argv) > 1 else (HERE / "index.html").resolve().as_uri()
TODAY = "2026-10-29"
URL = BASE + "?today=" + TODAY
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)
KEY = "bar_member_demo_v1"
errors, results = [], []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("OK  " if cond else "NG  ") + name)


def num(s):
    return int("".join(ch for ch in s if ch.isdigit() or ch == "-") or 0)


def state(pg):
    return pg.evaluate(f"JSON.parse(localStorage.getItem('{KEY}'))")


def login(pg, email):
    pg.goto(URL + "#/login")
    pg.wait_for_selector("#loginBtn")
    pg.fill("#lemail", email)
    pg.fill("#lpw", "demo")
    pg.click("#loginBtn")
    pg.wait_for_selector("#scan")


def scan(pg, text):
    pg.goto(URL + "#/home")
    pg.wait_for_selector("#scan")
    pg.fill("#scan", text)
    pg.press("#scan", "Enter")
    pg.wait_for_timeout(150)


def decode(pg, selector):
    if zxingcpp is None:
        return None
    png = pg.locator(selector).first.screenshot()
    img = Image.open(io.BytesIO(png)).convert("L")
    w, h = img.size
    pad = Image.new("L", (w + 80, h + 80), 255)
    pad.paste(img, (40, 40))
    res = zxingcpp.read_barcodes(pad)
    return res[0].text if res else ""


def d(s):
    return date.fromisoformat(s)


def add_year_minus1(s):
    x = d(s)
    try:
        y = x.replace(year=x.year + 1)
    except ValueError:
        y = x.replace(year=x.year + 1, day=28)
    return (y - timedelta(days=1)).isoformat()


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark", accept_downloads=True, locale="ja-JP")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(URL)
    pg.evaluate("localStorage.clear(); sessionStorage.clear()")
    pg.reload()
    pg.goto(URL + "#/login")
    pg.wait_for_selector("#loginBtn")
    bg = pg.evaluate("getComputedStyle(document.body).backgroundColor")
    check(f"ダーク端末でも背景は明るい ({bg})", bg == "rgb(247, 245, 242)")
    pg.screenshot(path=str(SHOTS / "00_login.png"))

    # ── ログイン
    pg.fill("#lemail", "owner@example.com")
    pg.fill("#lpw", "wrong")
    pg.click("#loginBtn")
    check("誤ったパスワードはエラー表示", pg.is_visible("text=メールアドレスかパスワードが違います"))
    pg.fill("#lpw", "demo")
    pg.click("#loginBtn")
    pg.wait_for_selector("#scan")
    check("オーナーは5つの画面が見える", len(pg.query_selector_all("#nav a")) == 5)
    S = state(pg)
    check("見本の会員 120人", len(S["members"]) == 120)
    tv = [v for v in S["visits"] if v["date"] == TODAY]
    paid = [v for v in tv if v["amount"] is not None]
    check("本日の来店 4組", pg.inner_text("#kGroups") == "4組" and len(tv) == 4)
    check("在店中 2組", pg.inner_text("#kIn") == "2組")
    check(f"本日の売上が独立計算と一致 ({sum(v['amount'] for v in paid):,}円)", num(pg.inner_text("#kSales")) == sum(v["amount"] for v in paid))
    exp_over = sum(1 for m in S["members"] if m["feeUntil"] < TODAY)
    exp_soon = sum(1 for m in S["members"] if TODAY <= m["feeUntil"] <= (d(TODAY) + timedelta(days=30)).isoformat())
    check(f"年会費の期限切れ人数が一致 ({exp_over}人・6人以上)", num(pg.inner_text("#nOver")) == exp_over and exp_over >= 6)
    check(f"30日以内の人数が一致 ({exp_soon}人・5人以上)", num(pg.inner_text("#nSoon")) == exp_soon and exp_soon >= 5)
    exp_bover = sum(1 for x in S["bottles"] if x["status"] == "キープ中" and x["until"] < TODAY)
    check(f"保管期限切れボトル数が一致 ({exp_bover}本)", num(pg.inner_text("#nBottle")) == exp_bover)
    exp_bday = sum(1 for m in S["members"] if m["birth"][5:7] == TODAY[5:7])
    check(f"今月誕生日の人数が一致 ({exp_bday}人)", num(pg.inner_text("#nBday")) == exp_bday and exp_bday >= 1)
    check("受付欄に最初から入力できる", pg.evaluate("document.activeElement.id") == "scan")
    clip = pg.evaluate("[...document.querySelectorAll('.tbl-wrap')].filter(e=>e.scrollWidth>e.clientWidth+1).length")
    check(f"PCの受付で表が横に切れない (切れ={clip})", clip == 0)
    pg.screenshot(path=str(SHOTS / "01_home.png"), full_page=True)

    # ── 期限切れの会員を読み取る → 警告 → 年会費を受け取って来店
    m7 = next(m for m in S["members"] if m["no"] == "M0007")
    check("M0007 は期限切れの見本", m7["feeUntil"] < TODAY)
    scan(pg, "7")
    check("番号「7」で M0007 を呼び出せる", m7["name"] in pg.inner_text("#scanName"))
    check("期限切れの赤い警告が出る", pg.is_visible("#feeAlert") and "期限が切れています" in pg.inner_text("#feeAlert"))
    over_days = (d(TODAY) - d(m7["feeUntil"])).days
    check(f"超過日数の表示 ({over_days}日)", f"{over_days}日超過" in pg.inner_text("#feeAlert"))
    check("警告時は「年会費を受け取って来店」ボタンがある", pg.is_visible("#btnFeeIn") and pg.is_visible("#btnIn"))
    pg.screenshot(path=str(SHOTS / "02_scan_expired.png"), full_page=True)
    w0 = int(pg.inner_text("#stWomen"))
    if w0 == 0:
        pg.click("[data-st='women'][data-d='1']")
    else:
        pg.click("[data-st='men'][data-d='1']")
    check(f"人数を増やせる（性別の初期値＝{m7['gender']}・合計2名）", int(pg.inner_text("#stWomen")) + int(pg.inner_text("#stMen")) == 2 and (m7["gender"] == "男性") == (w0 == 0))
    pg.click("#btnFeeIn")
    pg.wait_for_timeout(200)
    S = state(pg)
    m7 = next(m for m in S["members"] if m["no"] == "M0007")
    check(f"年会費を受け取ると期限が受取日から1年に ({m7['feeUntil']})", m7["feeUntil"] == add_year_minus1(TODAY))
    fee_rec = [f for f in S["fees"] if f["mid"] == m7["id"] and f["date"] == TODAY]
    check("年会費の受け取り記録が1件増える（10,000円）", len(fee_rec) == 1 and fee_rec[0]["amount"] == 10000)
    check("在店中が3組に", pg.inner_text("#kIn") == "3組")
    new_v = [v for v in S["visits"] if v["mid"] == m7["id"] and v["date"] == TODAY]
    check("来店記録 2名（男1・女1）・会計前", len(new_v) == 1 and new_v[0]["men"] + new_v[0]["women"] == 2 and new_v[0]["amount"] is None)
    check("記録後は受付欄が空で次の人を読める", pg.input_value("#scan") == "" and not pg.is_visible("#mcard"))

    scan(pg, "*m0007*")
    check("リーダー形式（*m0007*・小文字）でも呼び出せる", m7["name"] in pg.inner_text("#scanName"))
    check("受け取り後は警告が消える", not pg.is_visible("#feeAlert"))
    check("同じ日に2回読むと在店中の案内", pg.is_visible("#already") and pg.is_visible("#toPay"))

    # ── 30日以内の会員
    m12 = next(m for m in S["members"] if m["no"] == "M0012")
    scan(pg, "M0012")
    check(f"M0012 はまもなく期限の案内 (〜{m12['feeUntil']})", pg.is_visible("#feeSoon") and not pg.is_visible("#feeAlert"))
    g0 = num(pg.inner_text("#kGuests"))
    pg.click("[data-st='men'][data-d='1']")
    pg.click("#btnIn")
    pg.wait_for_timeout(200)
    check("来店記録で客数が人数分増える", num(pg.inner_text("#kGuests")) == g0 + 2)

    # 見つからない番号・人数0
    scan(pg, "9999")
    check("見つからない番号は案内", pg.is_visible("#scanErr"))
    scan(pg, "1")
    check("M0001 のキープ中ボトルが受付に出る", "シングルモルト 12年" in pg.inner_text("#scanBottles"))
    check("M0001 のメモが受付に出る", pg.is_visible("text=ハイボールは濃いめ"))
    if pg.inner_text("#stMen") == "1":
        pg.click("[data-st='men'][data-d='-1']")
    pg.click("#btnIn")
    check("人数0では記録しない", pg.is_visible("text=人数を1名以上にしてください") and pg.is_visible("#mcard"))
    pg.click("#scanClose")

    # ── 会計
    pg.goto(URL + "#/home")
    pg.wait_for_selector("#todayList")
    sales0 = num(pg.inner_text("#kSales"))
    v7 = new_v[0]["id"]
    pg.goto(URL + f"#/visit/{v7}")
    pg.wait_for_selector("#vSave")
    check("会計画面にお客さまの名前", m7["name"] in pg.inner_text("#vWho"))
    pg.click("#vSave")
    check("金額なしは会計できない", pg.is_visible("text=お会計の金額を入力してください"))
    pg.fill("#vAmount", "-5")
    pg.click("#vSave")
    check("マイナスの金額は入らない", pg.is_visible("text=お会計は0以上の整数（円）で入力してください"))
    pg.fill("#vAmount", "32000")
    check("金額の円表示", pg.inner_text("#vAmountLbl") == "32,000円")
    pg.click("#vPay >> text=カード")
    pg.fill("#vDate", "2030-01-01")
    pg.click("#vSave")
    check("未来の日付は記録できない", pg.is_visible("text=未来の日付は記録できません"))
    pg.fill("#vDate", TODAY)
    pg.click("#vSave")
    pg.wait_for_selector("#scan")
    check("会計で本日の売上が 32,000円 増える", num(pg.inner_text("#kSales")) == sales0 + 32000)
    vv = next(v for v in state(pg)["visits"] if v["id"] == v7)
    check("お支払い方法がカードで保存", vv["pay"] == "カード" and vv["amount"] == 32000)

    # ビジター
    pg.click("#visitorBtn")
    pg.wait_for_selector("#vSave")
    pg.click("[data-st='men'][data-d='1']")
    pg.click("#vSave")
    pg.wait_for_selector("#scan")
    check("ビジターは金額空欄で在店中として記録", "会員以外（ビジター）" in pg.inner_text("#todayList") and pg.inner_text("#kIn") == "4組")

    # ── 会員一覧
    pg.click("nav >> text=会員")
    pg.wait_for_selector("#mList tr")
    check("一覧 120人", pg.inner_text("#mCount").startswith("120人を表示"))
    S = state(pg)
    over_now = sum(1 for m in S["members"] if m["feeUntil"] < TODAY)
    pg.select_option("#ffee", "over")
    check(f"年会費 期限切れで絞り込み ({over_now}人＝受け取ったM0007が減る)", num(pg.inner_text("#mCount").split("人")[0]) == over_now and over_now == exp_over - 1)
    pg.select_option("#ffee", "")
    pg.fill("#fq", "こうの")
    rows = pg.query_selector_all("#mList tr[data-m]")
    check(f"ふりがなで検索 ({len(rows)}人・全員が甲野)", len(rows) > 0 and all("甲野" in r.inner_text() for r in rows))
    pg.fill("#fq", "M0007")
    rows = pg.query_selector_all("#mList tr[data-m]")
    check("会員番号で検索", len(rows) == 1 and "M0007" in rows[0].inner_text())
    pg.fill("#fq", "ぜったいいない")
    check("該当なしの案内", pg.is_visible("text=条件に合う会員がいません"))
    pg.fill("#fq", "")
    pg.check("#fbday")
    check(f"今月誕生日で絞り込み ({exp_bday}人)", num(pg.inner_text("#mCount").split("人")[0]) == exp_bday)
    pg.uncheck("#fbday")
    pg.select_option("#fsort", "sum")
    sums = {}
    for v in S["visits"]:
        if v["mid"] and v["amount"] is not None:
            sums[v["mid"]] = sums.get(v["mid"], 0) + v["amount"]
    top = max(sums, key=sums.get)
    top_m = next(m for m in S["members"] if m["id"] == top)
    check(f"累計売上順の1位が独立計算と一致 ({top_m['no']} {sums[top]:,}円)", top_m["no"] in pg.query_selector_all("#mList tr[data-m]")[0].inner_text())
    with pg.expect_download() as dl:
        pg.click("#mCsv")
    text = Path(dl.value.path()).read_text(encoding="utf-8-sig")
    check("会員CSV 見出し＋120行", len(text.strip().splitlines()) == 121 and text.startswith("会員番号,氏名"))
    pg.select_option("#fsort", "no")
    pg.screenshot(path=str(SHOTS / "03_members.png"), full_page=True)

    # ── 会員の詳細
    m1 = next(m for m in S["members"] if m["no"] == "M0001")
    pg.goto(URL + f"#/member/{m1['id']}")
    pg.wait_for_selector("#mVisits")
    mv = [v for v in S["visits"] if v["mid"] == m1["id"]]
    ms = sum(v["amount"] for v in mv if v["amount"] is not None)
    check(f"来店回数が一致 ({len(mv)}回)", num(pg.inner_text("#mVisits")) == len(mv))
    check(f"累計売上が一致 ({ms:,}円)", num(pg.inner_text("#mSales")) == ms)
    code = decode(pg, "#app .bc")
    check(f"会員証のバーコードを読み取ると M0001 ({code})", code == "M0001")
    exp_next = add_year_minus1((d(m1["feeUntil"]) + timedelta(days=1)).isoformat())
    check(f"期限内の更新は今の期限の翌日から1年 ({exp_next})", pg.inner_text("#feeNext") == exp_next.replace("-", "/"))
    pg.once("dialog", lambda dg: dg.accept())
    pg.click("#feeBtn")
    pg.wait_for_timeout(200)
    check("年会費の受け取りで期限が延びる", pg.inner_text("#mFeeUntil") == exp_next.replace("-", "/"))
    pg.screenshot(path=str(SHOTS / "04_member.png"), full_page=True)
    pg.click("#nbAdd")
    check("ボトル名が空なら追加しない", pg.is_visible("text=ボトルの名前を入力してください"))
    pg.fill("#nbName", "ジン")
    pg.click("#nbAdd")
    pg.wait_for_timeout(200)
    check("ボトルを追加（保管期限は6か月後）", pg.is_visible("#mBottles >> text=ジン") and pg.is_visible("#mBottles >> text=2027/04/29"))
    n_keep = len(pg.query_selector_all("#mBottles [data-fin]"))
    pg.click("#mBottles tr:has-text('ジン') >> text=飲み切った")
    pg.wait_for_timeout(200)
    check("飲み切ったボトルは終了になる", len(pg.query_selector_all("#mBottles [data-fin]")) == n_keep - 1)

    # ── 会員の登録
    pg.goto(URL + "#/member/new")
    pg.wait_for_selector("#fSave")
    check("新しい会員番号を自動で振る (M0121)", pg.input_value("#fNo") == "M0121")
    check("年会費の期限を入会日から1年で自動入力", pg.input_value("#fUntil") == add_year_minus1(TODAY))
    pg.click("#fSave")
    check("入力漏れを全部表示", all(pg.is_visible(f"text={t}") for t in ["氏名を入力してください", "性別を選んでください"]))
    pg.fill("#fNo", "1")
    pg.fill("#fName", "<b>テスト</b> 会員")
    pg.click("#fGender >> text=女性")
    pg.click("#fSave")
    check("同じ会員番号はエラー", pg.is_visible("text=同じ会員番号の会員がすでにいます"))
    pg.fill("#fNo", "M0121")
    pg.fill("#fRef", "M9999")
    pg.click("#fSave")
    check("存在しない紹介者はエラー", pg.is_visible("text=紹介者の会員番号が見つかりません"))
    pg.fill("#fRef", "3")
    pg.fill("#fJoined", "2026-10-01")
    pg.dispatch_event("#fJoined", "change")
    check("入会日を変えると期限も追従", pg.input_value("#fUntil") == "2027-09-30")
    pg.click("#fSave")
    pg.wait_for_selector("#mTitle")
    check("登録後に詳細へ・タグは文字のまま表示（XSS無効）", "<b>テスト</b>" in pg.inner_text("#mTitle"))
    check("紹介者のリンク", pg.is_visible("a:has-text('M0003')"))
    check("入会時の年会費が記録される", len(pg.query_selector_all("#feeList tr")) == 1 and "2027/09/30" in pg.inner_text("#feeList"))
    scan(pg, "121")
    check("登録した会員をすぐ受付で呼び出せる", "テスト" in pg.inner_text("#scanName"))
    pg.click("#scanClose")

    # ── ボトル一覧
    pg.click("nav >> text=ボトルキープ")
    pg.wait_for_selector("#bList")
    S = state(pg)
    keep = sum(1 for x in S["bottles"] if x["status"] == "キープ中")
    check(f"キープ中のボトル数が一致 ({keep}本)", num(pg.inner_text("#bCount")) == keep)
    pg.click("#bSeg >> text=保管期限切れ")
    check(f"保管期限切れで絞り込み ({exp_bover}本)", num(pg.inner_text("#bCount")) == exp_bover)
    pg.click("#bSeg >> text=キープ中すべて")
    first = pg.query_selector("[data-lv]")
    bid = int(first.get_attribute("data-lv"))
    pg.select_option(f"[data-lv='{bid}']", "少ない")
    check("残りの量を更新", next(x for x in state(pg)["bottles"] if x["id"] == bid)["level"] == "少ない")

    # ── 売上レポート（今月）
    pg.click("nav >> text=売上レポート")
    pg.wait_for_selector("#rSales")
    S = state(pg)
    ms0 = TODAY[:8] + "01"

    def stat(f, t):
        vs = [v for v in S["visits"] if v["amount"] is not None and f <= v["date"] <= t]
        sales = sum(v["amount"] for v in vs)
        guests = sum(v["men"] + v["women"] for v in vs)
        men = sum(v["men"] for v in vs)
        return vs, sales, guests, men

    vs, sales, guests, men = stat(ms0, TODAY)
    check(f"今月の売上が独立計算と一致 ({sales:,}円)", num(pg.inner_text("#rSales")) == sales)
    check(f"今月の組数が一致 ({len(vs)}組)", num(pg.inner_text("#rGroups")) == len(vs))
    check(f"今月の客数が一致 ({guests}名)", num(pg.inner_text("#rGuests")) == guests)
    check(f"客単価＝売上÷客数 ({round(sales / guests):,}円)", num(pg.inner_text("#rAvg")) == round(sales / guests))
    mp = round(men / guests * 100)
    check(f"男女比が一致 (男性{mp}%)", f"男性 {mp}%" in pg.inner_text("#rRatio"))
    _, psales, _, _ = stat("2026-09-01", "2026-09-29")
    check(f"前の期間（先月の同じ日まで）と比較 ({psales:,}円)", f"{psales:,}円" in pg.inner_text("#rSales >> xpath=.."))
    fee_m = sum(f["amount"] for f in S["fees"] if ms0 <= f["date"] <= TODAY)
    check(f"年会費は売上と別に集計 ({fee_m:,}円)", num(pg.inner_text("#rFee")) == fee_m)
    check("月ごとの推移 12か月", len(pg.query_selector_all("#rTrend tr")) == 12)
    trend_last = pg.query_selector_all("#rTrend tr")[-1].query_selector_all("td")[1].inner_text()
    check("推移の今月＝上の売上", num(trend_last) == sales)
    check("日ごとの棒グラフ（29日分）", len(pg.query_selector_all("#rBars .b")) == 29)
    rank = {}
    for v in vs:
        if v["mid"]:
            rank[v["mid"]] = rank.get(v["mid"], 0) + v["amount"]
    r1 = max(rank, key=rank.get)
    r1m = next(m for m in S["members"] if m["id"] == r1)
    check(f"会員ごとの売上1位が一致 ({r1m['no']} {rank[r1]:,}円)", r1m["no"] in pg.query_selector_all("#rRank tr")[0].inner_text() and num(pg.query_selector_all("#rRank tr")[0].query_selector_all("td")[2].inner_text()) == rank[r1])
    wd_sum = sum(num(r.query_selector_all("td")[1].inner_text()) for r in pg.query_selector_all("#rWd tr"))
    check("曜日ごとの合計＝売上", wd_sum == sales)
    check("日曜は0円（定休日の見本）", num(pg.query_selector_all("#rWd tr")[6].query_selector_all("td")[1].inner_text()) == 0)
    last = {}
    for v in S["visits"]:
        if v["mid"] and v["date"] > last.get(v["mid"], ""):
            last[v["mid"]] = v["date"]
    away = [m for m in S["members"] if m["feeUntil"] >= TODAY and (m["id"] not in last or (d(TODAY) - d(last[m["id"]])).days >= 60)]
    check(f"60日以上来ていない会員の人数が一致 ({len(away)}人)", num(pg.inner_text("#rAway")) == len(away))
    clip = pg.evaluate("[...document.querySelectorAll('.tbl-wrap')].filter(e=>e.scrollWidth>e.clientWidth+1).length")
    check(f"PCのレポートで表が横に切れない (切れ={clip})", clip == 0)
    pg.screenshot(path=str(SHOTS / "05_report.png"), full_page=True)
    with pg.expect_download() as dl:
        pg.click("#csvDetail")
    t = Path(dl.value.path()).read_text(encoding="utf-8-sig")
    check(f"来店明細CSV 見出し＋{len(vs)}行", len(t.strip().splitlines()) == len(vs) + 1 and t.startswith("日付,時刻,会員番号"))
    with pg.expect_download() as dl:
        pg.click("#csvSum")
    t = Path(dl.value.path()).read_text(encoding="utf-8-sig")
    check("集計CSVに売上の行", f"売上（飲食）,{sales}," in t)

    # 週
    pg.click("#rSeg >> text=今週")
    ws = (d(TODAY) - timedelta(days=d(TODAY).weekday())).isoformat()
    vs_w, sales_w, _, _ = stat(ws, TODAY)
    check(f"今週＝月曜から ({ws}・{sales_w:,}円)", num(pg.inner_text("#rSales")) == sales_w and "10/26（月）" in pg.inner_text("#periodLbl"))
    check("週ごとの推移 8週", len(pg.query_selector_all("#rTrend tr")) == 8)
    pg.click("#rSeg >> text=先月")
    check("先月＝9/1〜9/30", "2026/09/01（火）〜2026/09/30（水）" in pg.inner_text("#periodLbl"))
    _, s9, _, _ = stat("2026-09-01", "2026-09-30")
    check(f"先月の売上 ({s9:,}円)", num(pg.inner_text("#rSales")) == s9)
    pg.click("#rSeg >> text=期間を指定")
    pg.fill("#rFrom", "2026-10-10")
    pg.fill("#rTo", "2026-10-01")
    pg.click("#rApply")
    check("はじめ＞おわりは案内", pg.is_visible("text=期間の「はじめ」と「おわり」を正しく入れてください"))
    pg.fill("#rFrom", "2026-10-01")
    pg.fill("#rTo", "2026-10-10")
    pg.click("#rApply")
    _, s10, _, _ = stat("2026-10-01", "2026-10-10")
    check(f"期間指定の売上 ({s10:,}円)・くらべる期間は直前10日", num(pg.inner_text("#rSales")) == s10 and "2026/09/21〜2026/09/30" in pg.inner_text("#periodLbl"))

    # ── 設定
    pg.click("nav >> text=設定")
    pg.wait_for_selector("#sSave")
    pg.fill("#sFee", "12000")
    pg.click("#sSave")
    m20 = next(m for m in S["members"] if m["no"] == "M0020")
    scan(pg, "20")
    check("年会費の金額を変えると受付の案内も変わる", m20["feeUntil"] < TODAY and "12,000円" in pg.inner_text("#feeAlert"))
    pg.click("#scanClose")
    pg.goto(URL + "#/settings")
    pg.wait_for_selector("#uAdd")
    pg.fill("#uName", "テスト店員")
    pg.fill("#uMail", "bad")
    pg.click("#uAdd")
    check("メールの形が違うとエラー", pg.is_visible("text=メールアドレスを正しく入力してください"))
    pg.fill("#uMail", "staff@example.com")
    pg.click("#uAdd")
    check("同じメールはエラー", pg.is_visible("text=同じメールアドレスの人がすでにいます"))
    pg.fill("#uMail", "new@example.com")
    pg.click("#uAdd")
    check("スタッフを追加", pg.is_visible("#userList >> text=テスト店員"))
    check("自分の権限は変えられない", pg.eval_on_selector("[data-ur='1']", "e=>e.disabled"))

    # 名簿の取り込み
    csv_text = "﻿会員番号,氏名,ふりがな,性別,生年月日,電話,入会日,年会費の期限,メモ\r\n" \
               ",子安 一,こやす はじめ,男,1985/4/1,090-1111-2222,2025/11/03,,窓際\r\n" \
               "M0500,\"卯月 \"\"葵\"\"\",うづき あおい,女性,,,2026-01-15,2027-01-14,\"席,奥\"\r\n" \
               ",,なし,男性,,,,,\r\n" \
               ",戌亥 太,いぬい ふとし,不明,,,,,\r\n" \
               "M0001,重複 太郎,,男性,,,,,\r\n" \
               ",日付 違い,,女性,1990/13/40,,,,\r\n"
    imp = HERE / "shots" / "_import_test.csv"
    imp.write_text(csv_text, encoding="utf-8")
    pg.set_input_files("#impFile", str(imp))
    pg.wait_for_selector("#impSummary")
    check("取り込み：正しい2件・直す4件", pg.inner_text("#impSummary") == "取り込める行：2件／直す必要がある行：4件")
    et = pg.inner_text("#impErrors")
    check("取り込み：行番号つきで理由を表示", all(s in et for s in ["4行目", "氏名が空です", "5行目", "性別は", "6行目", "M0001 はすでに使われています", "7行目", "生年月日の形が違います"]))
    pg.click("#impGo")
    pg.wait_for_timeout(200)
    S = state(pg)
    a = next((m for m in S["members"] if m["name"] == "子安 一"), None)
    bq = next((m for m in S["members"] if m["no"] == "M0500"), None)
    check("番号なしの行は自動採番・期限は入会日から1年", a is not None and a["no"] == "M0122" and a["feeUntil"] == "2026-11-02" and a["gender"] == "男性")
    check("引用符・カンマ入りの欄も崩れない", bq is not None and bq["name"] == '卯月 "葵"' and bq["memo"] == "席,奥")
    check("取り込み後の会員 123人", len(S["members"]) == 123)
    pg.screenshot(path=str(SHOTS / "06_settings.png"), full_page=True)

    # 再読み込みしても残る
    pg.reload()
    pg.wait_for_selector("#sSave")
    check("再読み込みしても入力が残る", len(state(pg)["members"]) == 123 and pg.input_value("#sFee") == "12000")
    pg.click("text=ログアウト")

    # ── スタッフの権限
    login(pg, "staff@example.com")
    check("スタッフは3つの画面だけ", len(pg.query_selector_all("#nav a")) == 3)
    pg.goto(URL + "#/report")
    pg.wait_for_timeout(150)
    check("売上レポートはURL直打ちでも拒否", pg.is_visible("text=この画面を開く権限がありません"))
    pg.goto(URL + "#/settings")
    pg.wait_for_timeout(150)
    check("設定もURL直打ちで拒否", pg.is_visible("text=この画面を開く権限がありません"))
    pg.goto(URL + "#/members")
    pg.wait_for_selector("#mList tr")
    check("スタッフには会員CSVのボタンが無い", pg.query_selector("#mCsv") is None)
    check("前の人の絞り込みが残らない", pg.input_value("#fq") == "" and pg.input_value("#ffee") == "")
    pg.goto(URL + f"#/visit/{v7}")
    pg.wait_for_selector("#vSave")
    check("スタッフは来店記録を削除できない", pg.query_selector("#vDel") is None)
    pg.click("text=ログアウト")

    # ── 印刷（会員証シール）
    login(pg, "owner@example.com")
    pg.goto(URL + "#/members")
    pg.wait_for_selector("#mList tr")
    for no in ("M0002", "M0003", "M0004"):
        pg.check(f"[aria-label='{no}を選ぶ']")
    check("選んだ人数がボタンに出る", "選んだ3人" in pg.inner_text("#printSel"))
    pg.click("#printSel")
    pg.wait_for_selector("#sheet")
    check("シール 3枚", len(pg.query_selector_all(".sticker")) == 3)
    code = decode(pg, ".sticker[data-no='M0003'] .bc")
    check(f"シールのバーコードを読み取ると M0003 ({code})", code == "M0003")
    pg.emulate_media(media="print")
    check("印刷時はメニューと案内を隠す", not pg.is_visible("#hdr") and not pg.is_visible("#doPrint"))
    pg.emulate_media(media="screen")
    pg.screenshot(path=str(SHOTS / "07_print.png"), full_page=True)

    # ── 日付が変わっても状態が保たれる（1週間後に開く）
    pg2 = ctx.new_page()
    pg2.goto(BASE + "?today=2026-11-05#/login")
    pg2.click("[data-quick='1']")
    pg2.wait_for_selector("#scan")
    S2 = state(pg2)
    over2 = sum(1 for m in S2["members"] if m["feeUntil"] < "2026-11-05")
    check(f"日付をずらしても期限切れの人数は同じ見え方 ({over2}人)", num(pg2.inner_text("#nOver")) == over2)
    pg2.close()
    pg.goto(URL + "#/home")

    # ── スマホ幅
    m = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True, has_touch=True, locale="ja-JP")
    mp = m.new_page()
    mp.on("pageerror", lambda e: errors.append(str(e)))
    mp.goto(URL + "#/login")
    mp.click("[data-quick='1']")
    mp.wait_for_selector("#scan")
    mp.fill("#scan", "7")
    mp.press("#scan", "Enter")
    for route, shot in [("#/home", "08_sp_home"), ("#/members", "09_sp_members"), ("#/member/1", "10_sp_member"), ("#/report", "11_sp_report"), ("#/bottles", "12_sp_bottles"), ("#/visit/" + str(v7), "13_sp_visit"), ("#/print?ids=1,2", "14_sp_print"), ("#/settings", "15_sp_settings")]:
        mp.goto(URL + route)
        mp.wait_for_timeout(300)
        sw = mp.evaluate("document.documentElement.scrollWidth")
        check(f"スマホで横はみ出しなし {route} ({sw}px)", sw <= 390)
        if route in ("#/home", "#/report", "#/members"):
            clip = mp.evaluate("[...document.querySelectorAll('.tbl-wrap')].filter(e=>e.scrollWidth>e.clientWidth+1).length")
            check(f"スマホ {route} で表が横に切れない (切れ={clip})", clip == 0)
        mp.screenshot(path=str(SHOTS / f"{shot}.png"), full_page=True)
    mp.goto(URL + "#/home")
    mp.fill("#scan", "7")
    mp.press("#scan", "Enter")
    mp.wait_for_timeout(200)
    sw = mp.evaluate("document.documentElement.scrollWidth")
    check(f"スマホで会員を呼び出してもはみ出さない ({sw}px)", sw <= 390)
    mp.screenshot(path=str(SHOTS / "16_sp_scan.png"), full_page=True)

    # ── データを最初に戻す
    pg.goto(URL + "#/login")
    pg.once("dialog", lambda dg: dg.accept())
    pg.click("#resetData")
    n = pg.evaluate(f"JSON.parse(localStorage.getItem('{KEY}')).members.length")
    check("最初の状態に戻す（120人に戻る）", n == 120)

    check(f"画面のエラー 0件 {errors[:3]}", not errors)
    b.close()

ng = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(ng)}/{len(results)} 合格")
sys.exit(1 if ng else 0)
