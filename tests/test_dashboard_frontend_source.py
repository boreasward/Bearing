from pathlib import Path

# Svelte 5 frontend structure assertions (C9 — replaced vanilla-TS)


def test_svelte_entrypoint_and_app_root():
    assert Path("web/src/main.ts").exists()
    assert Path("web/src/app.svelte").exists()
    main = Path("web/src/main.ts").read_text(encoding="utf-8")
    assert "mount" in main
    assert "app.svelte" in main


def test_svelte_route_views_exist():
    # v2.3 adds Data; v2.11 adds the standalone Account menu.
    routes = ["Today", "Instrument", "Account", "Review", "Data", "Tools", "Settings"]
    for r in routes:
        path = Path(f"web/src/routes/{r}.svelte")
        assert path.exists(), str(path)


def test_svelte_shared_lib_exists():
    expected = [
        "web/src/lib/api.ts",
        "web/src/lib/sse.ts",
        "web/src/lib/theme.ts",
        "web/src/lib/format.ts",
        "web/src/lib/router.ts",
    ]
    for f in expected:
        assert Path(f).exists(), f


def test_svelte_components_exist():
    expected = [
        "web/src/lib/components/AppShell.svelte",
        "web/src/lib/components/SideNav.svelte",
        "web/src/lib/ui/domain/StatusPill.svelte",
        "web/src/lib/ui/sheet/",
        "web/src/lib/ui/skeleton/",
    ]
    for f in expected:
        assert Path(f).exists(), f


def test_svelte_chart_wrappers_exist():
    expected = [
        "web/src/lib/charts/EChart.svelte",
        "web/src/lib/charts/CandleChart.svelte",
    ]
    for f in expected:
        assert Path(f).exists(), f


def test_api_client_exports_core_functions():
    api = Path("web/src/lib/api.ts").read_text(encoding="utf-8")
    for fn in ["getStatus", "getHealth", "getWatchlist", "getUSResearch",
               "syncUSResearch", "pollRun", "getFilingRecords"]:
        assert fn in api, f"missing: {fn}"


def test_app_hash_router_covers_all_zones():
    # v2.11 adds #account/<id>/<tab>; the earlier zones and legacy fallbacks remain covered.
    router = Path("web/src/lib/router.ts").read_text(encoding="utf-8")
    assert "export type Zone = 'today' | 'instrument' | 'account' | 'review' | 'data' | 'knowledge' | 'tools' | 'settings' | 'plan' | 'discuss'" in router
    assert "export const workbenchZones: Zone[] = ['today', 'instrument', 'account', 'review']" in router
    assert "export const zones: Zone[] = [...workbenchZones, 'data', 'knowledge', 'tools', 'settings', 'plan', 'discuss']" in router
    for zone in ["technicals", "strategy", "trade"]:
        assert f"'{zone}'" not in router, f"legacy zone still routed: {zone}"
    app = Path("web/src/app.svelte").read_text(encoding="utf-8")
    assert "hashchange" in app
    assert "parseHash" in app


def test_design_tokens_css_exists():
    assert Path("web/src/styles/tokens.css").exists()
    tokens = Path("web/src/styles/tokens.css").read_text(encoding="utf-8")
    for var in ["--background", "--card", "--primary", "--up", "--down"]:
        assert var in tokens, f"missing token: {var}"


def test_tailwind_v4_css_first():
    import json

    app_css = Path("web/src/styles/app.css").read_text(encoding="utf-8")
    assert '@import "tailwindcss"' in app_css or "@import 'tailwindcss'" in app_css
    assert "@tailwind" not in app_css
    assert not Path("web/tailwind.config.ts").exists()
    assert not Path("web/postcss.config.js").exists()
    pkg = json.loads(Path("web/package.json").read_text(encoding="utf-8"))
    dev = pkg["devDependencies"]
    assert dev["tailwindcss"] == "4.3.3"
    assert dev["@tailwindcss/vite"] == "4.3.3"
    assert "autoprefixer" not in dev and "postcss" not in dev
    assert "@tailwindcss/vite" in Path("web/vite.config.ts").read_text(encoding="utf-8")


def test_no_legacy_token_names():
    import re

    class_re = re.compile(
        r"\b(?:[\w-]+:)*(?:bg-bg|bg-surface(?:-2)?|text-ink|(?:text|border)-muted(?!-foreground)"
        r"|(?:text|bg|border|ring)-accent|(?:text|bg|border)-danger|shadow-elev-[12])\b"
    )
    var_re = re.compile(r"var\(--(?:bg|surface(?:-2)?|text(?:-muted)?|accent|danger|elev-[12])\)")
    offenders = []
    for path in Path("web/src").rglob("*"):
        if path.suffix not in {".svelte", ".ts", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        for m in list(class_re.finditer(text)) + list(var_re.finditer(text)):
            offenders.append(f"{path}: {m.group(0)}")
    assert not offenders, offenders[:20]


def test_dark_mode_token_override_exists():
    tokens = Path("web/src/styles/tokens.css").read_text(encoding="utf-8")
    assert "data-theme" in tokens


def test_sse_store_has_reconnect_logic():
    sse = Path("web/src/lib/sse.ts").read_text(encoding="utf-8")
    assert "EventSource" in sse
    assert "onerror" in sse
    assert "readable" in sse


def test_old_vanilla_ts_directories_are_deleted():
    for path in ["web/src/app", "web/src/features", "web/src/shared", "web/src/i18n", "web/src/styles.css"]:
        assert not Path(path).exists(), f"should be deleted: {path}"


