from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_mini_app_theme_is_dark_before_react_and_persists_manual_choice() -> None:
    html = (REPO_ROOT / "frontend/welcome/app.html").read_text(encoding="utf-8")
    source = (REPO_ROOT / "frontend/welcome/src/main.tsx").read_text(encoding="utf-8")
    styles = (REPO_ROOT / "frontend/welcome/src/styles.css").read_text(encoding="utf-8")

    assert 'localStorage.getItem("gramly-hello-theme")' in html
    assert 'saved === "light" ? "light" : "dark"' in html
    assert "document.documentElement.dataset.gramlyTheme = theme" in html
    assert 'localStorage.setItem("gramly-hello-theme",theme)' in source
    assert 'data-gramly-theme="light"' in styles
    assert "--app-bg:var(--tg-theme-bg-color" not in styles


def test_control_center_exposes_invoice_section_and_safe_reconciliation() -> None:
    source = (REPO_ROOT / "frontend/welcome/src/admin.tsx").read_text(encoding="utf-8")

    assert 'name:"Инвойсы"' in source
    assert 'payment_status:status' in source
    assert 'Сверить с провайдером' in source
    assert 'Скачать CSV' in source
    assert 'section==="Инвойсы"&&<Invoices initialStatus={invoiceStatusFilter}/>' in source


def test_control_center_dashboard_links_to_filtered_owners_and_invoices() -> None:
    app = (REPO_ROOT / "frontend/welcome/src/admin.tsx").read_text(encoding="utf-8")
    dashboard = (REPO_ROOT / "frontend/welcome/src/admin-dashboard.tsx").read_text(encoding="utf-8")

    assert 'segment=${segment}' in app
    assert 'initialSegment={ownerSegment}' in app
    assert 'openOwners("high_activity")' in dashboard
    assert 'openOwners("with_audience")' in dashboard
    assert 'openInvoices("settled")' in dashboard
    assert 'aria-pressed={days === value}' in dashboard


def test_mini_app_bootstrap_has_timeout_uuid_fallback_and_error_boundary() -> None:
    html = (REPO_ROOT / "frontend/welcome/app.html").read_text(encoding="utf-8")
    source = (REPO_ROOT / "frontend/welcome/src/main.tsx").read_text(encoding="utf-8")
    api = (REPO_ROOT / "frontend/welcome/src/api.ts").read_text(encoding="utf-8")

    assert "boot-fallback" in html
    assert "AppErrorBoundary" in source
    assert "Promise.allSettled" in source
    assert "finally" in source and "setReady(true)" in source
    assert "fetchWithTimeout" in api and "AbortController" in api
    assert "crypto.randomUUID()" not in api
    assert "getRandomValues" in api


def test_editor_exposes_four_per_button_styles_and_learning_uses_one_player() -> None:
    composer = (REPO_ROOT / "frontend/welcome/src/editor/composer.tsx").read_text(encoding="utf-8")
    learning = (REPO_ROOT / "frontend/welcome/src/audience.tsx").read_text(encoding="utf-8")

    for style in ("default", "primary", "success", "danger"):
        assert f'["{style}",' in composer
    assert 'role="radiogroup"' in composer
    assert 'role="radio"' in composer
    assert learning.count("<video key={active.id}") == 1
    assert 'onPause={event=>void saveProgress(active,event.currentTarget.currentTime,"progress",true)}' in learning
    assert 'value.progress.next_video_id' in learning
