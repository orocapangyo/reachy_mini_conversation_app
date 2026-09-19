"""Render architecture_diagram.html to high-resolution PNG using Playwright/Selenium/Headless Browser."""

import os
from pathlib import Path


async def render_with_playwright(html_path: Path, output_png: Path):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 860}, device_scale_factor=2)
        await page.goto(html_path.as_uri())
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(output_png), full_page=True)
        await browser.close()


def render_with_selenium(html_path: Path, output_png: Path):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1200,860")
    options.add_argument("--force-device-scale-factor=2")
    options.add_argument("--hide-scrollbars")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(html_path.as_uri())
        import time
        time.sleep(1)
        driver.save_screenshot(str(output_png))
    finally:
        driver.quit()


def render_with_edge(html_path: Path, output_png: Path):
    import subprocess
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    browser_exe = next((p for p in edge_paths if os.path.exists(p)), None)
    if not browser_exe:
        raise RuntimeError("No Chromium/Edge browser found.")

    cmd = [
        browser_exe,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=1200,860",
        f"--screenshot={str(output_png)}",
        html_path.as_uri(),
    ]
    subprocess.run(cmd, check=True)


def main():
    repo_root = Path(__file__).resolve().parent.parent
    html_path = repo_root / "docs" / "oss_report" / "assets" / "architecture_diagram.html"
    output_png = repo_root / "docs" / "oss_report" / "assets" / "architecture_diagram.png"
    output_png.parent.mkdir(parents=True, exist_ok=True)

    print(f"Rendering {html_path} -> {output_png}")
    try:
        render_with_edge(html_path, output_png)
        print(f"Success! Exported to {output_png}")
    except Exception as e:
        print(f"Edge headless failed ({e}), trying selenium...")
        try:
            render_with_selenium(html_path, output_png)
            print(f"Success! Exported via selenium to {output_png}")
        except Exception as e2:
            print(f"Selenium failed: {e2}")

    # Also copy to docs/assets
    doc_assets_png = repo_root / "docs" / "assets" / "architecture_diagram.png"
    doc_assets_png.parent.mkdir(parents=True, exist_ok=True)
    if output_png.exists():
        import shutil
        shutil.copyfile(output_png, doc_assets_png)
        print(f"Copied to {doc_assets_png}")


if __name__ == "__main__":
    main()
