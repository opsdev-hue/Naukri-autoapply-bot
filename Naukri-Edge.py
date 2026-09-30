"""
Naukri Auto-Apply Bot - Microsoft Edge Version (Updated for Naukri.com 2026)
================================================================================
Automates job applications on Naukri.com using Selenium with Edge browser.
Opens each job keyword search in a separate tab for faster collection.

Usage:
    1. Copy .env.example to .env and fill in your details
    2. pip install -r requirements.txt
    3. python Naukri-Edge.py

Requirements:
    - Edge browser installed
    - Dependencies from requirements.txt
"""

import os
import time
import logging
import pandas as pd
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from bs4 import BeautifulSoup

# Try to use webdriver-manager for automatic driver management
try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
load_dotenv()

# --- Naukri Credentials ---
NAUKRI_EMAIL = os.getenv('NAUKRI_EMAIL', '')
NAUKRI_PASSWORD = os.getenv('NAUKRI_PASSWORD', '')

# --- Personal Details ---
FIRSTNAME = os.getenv('FIRSTNAME', '')
LASTNAME = os.getenv('LASTNAME', '')

# --- Job Search ---
KEYWORDS = [kw.strip() for kw in os.getenv('KEYWORDS', '').split(',') if kw.strip()]
LOCATION = os.getenv('LOCATION', '').strip()

# --- Limits ---
MAX_APPLICATIONS = int(os.getenv('MAX_APPLICATIONS', '50'))
PAGES_PER_KEYWORD = int(os.getenv('PAGES_PER_KEYWORD', '2'))

# --- Edge Driver Path (optional, only if NOT using webdriver-manager) ---
EDGE_DRIVER_PATH = os.getenv('EDGE_DRIVER_PATH', '')

LOGIN_WAIT_TIMEOUT = int(os.getenv('LOGIN_WAIT_TIMEOUT', '0'))  # 0 = wait indefinitely
LOGIN_FORM_WAIT = int(os.getenv('LOGIN_FORM_WAIT', '20'))
LOGIN_POLL_SECONDS = 2

# ------------------------------------------------------------
# Logging Setup
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def validate_config():
    """Check that required configuration values are set."""
    errors = []
    if not NAUKRI_EMAIL:
        errors.append("NAUKRI_EMAIL is not set in .env")
    if not NAUKRI_PASSWORD:
        errors.append("NAUKRI_PASSWORD is not set in .env")
    if not KEYWORDS:
        errors.append("KEYWORDS is not set in .env (comma-separated job roles)")
    if not FIRSTNAME:
        errors.append("FIRSTNAME is not set in .env")
    if not LASTNAME:
        errors.append("LASTNAME is not set in .env")
    if errors:
        for e in errors:
            logger.error(e)
        logger.error("Please copy .env.example to .env and fill in your details.")
        return False
    return True


def create_edge_driver():
    """Create and return an Edge WebDriver instance."""
    options = webdriver.EdgeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    # Uncomment the line below to run in headless mode (no visible browser)
    # options.add_argument("--headless=new")

    if WEBDRIVER_MANAGER_AVAILABLE:
        logger.info("Using webdriver-manager to auto-download EdgeDriver...")
        service = EdgeService(EdgeChromiumDriverManager().install())
    elif EDGE_DRIVER_PATH:
        logger.info(f"Using EdgeDriver at: {EDGE_DRIVER_PATH}")
        service = EdgeService(executable_path=EDGE_DRIVER_PATH)
    else:
        logger.info("No driver path specified; assuming EdgeDriver is in PATH...")
        service = EdgeService()

    driver = webdriver.Edge(service=service, options=options)
    return driver


def _find_visible(driver, selectors):
    """Return the first visible/enabled element matching any selector."""
    for by, selector in selectors:
        try:
            for element in driver.find_elements(by, selector):
                if element.is_displayed() and element.is_enabled():
                    return element
        except Exception:
            continue
    return None


def _login_username_selectors():
    return [
        (By.ID, "usernameField"),
        (By.NAME, "usernameField"),
        (By.NAME, "USERNAME"),
        (By.ID, "emailTxt"),
        (By.CSS_SELECTOR, "input[placeholder*='Email ID']"),
        (By.CSS_SELECTOR, "input[type='email']")
    ]


def _login_password_selectors():
    return [
        (By.ID, "passwordField"),
        (By.NAME, "PASSWORD"),
        (By.ID, "pwd1"),
        (By.CSS_SELECTOR, "input[type='password']")
    ]


def _login_completed(driver):
    """Detect successful authentication instead of assuming a fixed sleep."""
    try:
        url = driver.current_url.lower()
        still_login_url = (
            "login.naukri.com" in url
            or "/nlogin/login" in url
            or "/nlogin/" in url
        )
        username = _find_visible(driver, _login_username_selectors())
        password = _find_visible(driver, _login_password_selectors())

        if not still_login_url and not username and not password:
            return True
        if "/mnjuser/" in url and not username and not password:
            return True
    except Exception:
        pass
    return False


def _login_error_text(driver):
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        phrases = (
            "invalid password", "incorrect password", "invalid email",
            "invalid username", "invalid credentials", "unable to login",
            "login failed", "enter valid email", "enter valid password",
        )
        return any(p in body for p in phrases)
    except Exception:
        return False


def _manual_verification_required(driver):
    """Detect OTP/CAPTCHA/security checks; do not attempt to bypass them."""
    try:
        otp_selectors = [
            (By.XPATH, "//input[contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'otp')]"),
            (By.XPATH, "//input[contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'otp')]"),
            (By.XPATH, "//input[contains(translate(@placeholder,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'otp')]"),
        ]
        if _find_visible(driver, otp_selectors):
            return True

        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        phrases = (
            "enter otp", "otp verification", "one time password",
            "verification code", "verify your identity", "captcha",
            "security check", "verify you are human", "robot",
        )
        return any(p in body for p in phrases)
    except Exception:
        return False


def _click_login_submit(driver, password_element):
    try:
        form = password_element.find_element(By.XPATH, "./ancestor::form[1]")
        buttons = form.find_elements(
            By.CSS_SELECTOR,
            "button[type='submit'], input[type='submit'], button"
        )
        for button in buttons:
            try:
                if button.is_displayed() and button.is_enabled():
                    driver.execute_script("arguments[0].click();", button)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    try:
        password_element.send_keys(Keys.ENTER)
        return True
    except Exception:
        return False


def _wait_for_login_completion(driver):
    """Wait until authenticated; user completes OTP/CAPTCHA in the visible browser."""
    start = time.monotonic()
    timeout = LOGIN_WAIT_TIMEOUT
    challenge_reported = False

    logger.info("Waiting for Naukri authentication to finish...")
    if timeout == 0:
        logger.info("LOGIN_WAIT_TIMEOUT=0: waiting indefinitely.")

    while timeout == 0 or (time.monotonic() - start) < timeout:
        # Check verification first so a security/OTP page is never mistaken for a completed login.
        if _manual_verification_required(driver):
            if not challenge_reported:
                logger.info("🔐 OTP/CAPTCHA/security verification detected.")
                logger.info("👉 Complete it manually in the open browser window.")
                logger.info("⏳ The bot will continue automatically after successful login.")
                challenge_reported = True
        elif _login_completed(driver):
            logger.info("✓ Naukri login verified.")
            return True

        if _login_error_text(driver):
            logger.error("Naukri reported invalid/failed login credentials.")
            return False

        time.sleep(LOGIN_POLL_SECONDS)

    logger.error(f"Login wait timed out after {timeout} seconds.")
    return False


def login_naukri(driver):
    """Robust Naukri login with manual OTP/CAPTCHA waiting."""
    logger.info("Logging in to Naukri.com...")

    login_urls = [
        "https://www.naukri.com/nlogin/login",
        "https://login.naukri.com/nLogin/Login.php",
    ]

    for login_url in login_urls:
        try:
            driver.get(login_url)
        except WebDriverException as exc:
            logger.warning(f"Could not open {login_url}: {exc}")
            continue

        deadline = time.monotonic() + LOGIN_FORM_WAIT

        while time.monotonic() < deadline:
            if _login_completed(driver):
                logger.info("✓ Existing Naukri session is already authenticated.")
                return True

            if _manual_verification_required(driver):
                return _wait_for_login_completion(driver)

            username = _find_visible(driver, _login_username_selectors())
            password = _find_visible(driver, _login_password_selectors())

            if username and password:
                try:
                    username.clear()
                    username.send_keys(NAUKRI_EMAIL)
                    password.clear()
                    password.send_keys(NAUKRI_PASSWORD)
                    if _click_login_submit(driver, password):
                        logger.info("Login form submitted; waiting for authentication result.")
                        break
                except Exception as exc:
                    logger.warning(f"Could not fill login form: {exc}")
                    break

            time.sleep(1)

        if _wait_for_login_completion(driver):
            return True

    logger.info("Automatic login detection did not complete.")
    logger.info("👉 Finish login manually in the open browser window.")
    return _wait_for_login_completion(driver)

def build_search_urls():
    """
    Build all search URLs for every keyword and page combination.
    Naukri.com updated URL format: /{keyword}-jobs-in-{location}
    Returns a list of (keyword, url) tuples.
    """
    urls = []
    for keyword in KEYWORDS:
        keyword_slug = keyword.lower().replace(' ', '-')
        for page_num in range(1, PAGES_PER_KEYWORD + 1):
            if not LOCATION:
                if page_num == 1:
                    url = f"https://www.naukri.com/{keyword_slug}-jobs"
                else:
                    url = f"https://www.naukri.com/{keyword_slug}-jobs-{page_num}"
            else:
                location_slug = LOCATION.lower().replace(' ', '-')
                if page_num == 1:
                    url = f"https://www.naukri.com/{keyword_slug}-jobs-in-{location_slug}"
                else:
                    url = f"https://www.naukri.com/{keyword_slug}-jobs-in-{location_slug}-{page_num}"
            urls.append((keyword, url))
    return urls


def open_tabs_parallel(driver, search_urls):
    """
    Open all search URLs in parallel browser tabs.
    The first URL opens in the current tab; the rest open in new tabs.
    Returns the original window handle.
    """
    original_window = driver.current_window_handle

    if not search_urls:
        return original_window

    # Open first URL in current tab
    keyword, first_url = search_urls[0]
    logger.info(f"[Tab: Main] Opening: {first_url}")
    driver.get(first_url)
    time.sleep(3)

    # Open remaining URLs in new tabs
    for keyword, url in search_urls[1:]:
        logger.info(f"[Tab: New] Opening: {url}")
        driver.switch_to.new_window('tab')
        driver.get(url)
        time.sleep(2)

    # Switch back to the original tab
    driver.switch_to.window(original_window)
    return original_window


def collect_job_links_from_tab(driver, window_handle):
    """
    Switch to a specific tab, scrape job links from the page.
    Uses updated Naukri.com selectors (as of 2026 redesign).

    Current HTML structure for job cards:
        <div class="srp-jobtuple-wrapper" data-job-id="...">
          <div class="cust-job-tuple layout-wrapper lay-2 sjw__tuple">
            <div class="row1">
              <h2><a class="title " href="https://...">Job Title</a></h2>
            </div>
          </div>
        </div>
    """
    links = []
    try:
        driver.switch_to.window(window_handle)
        time.sleep(3)  # Wait for page to fully render

        soup = BeautifulSoup(driver.page_source, 'html5lib')

        # New selector: find all job tuple wrappers
        job_wrappers = soup.find_all('div', class_='srp-jobtuple-wrapper')
        logger.info(f"[Tab: {driver.title[:50]}] Found {len(job_wrappers)} job cards (srp-jobtuple-wrapper)")

        if not job_wrappers:
            # Fallback: try the older cust-job-tuple class
            job_wrappers = soup.find_all('div', class_='cust-job-tuple')
            logger.info(f"[Tab: {driver.title[:50]}] Found {len(job_wrappers)} job cards (cust-job-tuple fallback)")

        for job_wrapper in job_wrappers:
            # Find the title link - new selector is a.title (class="title ")
            title_link = job_wrapper.find('a', class_='title')
            if title_link and title_link.get('href'):
                href = title_link.get('href')
                # Ensure full URL
                if href.startswith('/'):
                    href = 'https://www.naukri.com' + href
                links.append(href)

    except WebDriverException as e:
        logger.warning(f"[Tab] Error reading tab: {e}")

    return links


def collect_all_jobs_parallel(driver, search_urls):
    """
    Open all keyword search pages in parallel tabs and collect job links from each.
    Returns a deduplicated list of job URLs.
    """
    logger.info("=" * 50)
    logger.info(f"Opening {len(search_urls)} search pages in parallel tabs...")
    logger.info("=" * 50)

    # Open all search URLs in parallel tabs
    original_window = open_tabs_parallel(driver, search_urls)

    # Collect job links from each tab
    all_links = []
    window_handles = driver.window_handles
    logger.info(f"Collecting job links from {len(window_handles)} tabs...")

    for handle in window_handles:
        tab_links = collect_job_links_from_tab(driver, handle)
        all_links.extend(tab_links)

    # Close all extra tabs and return to main
    logger.info("Closing search tabs...")
    for handle in window_handles:
        if handle != original_window:
            try:
                driver.switch_to.window(handle)
                driver.close()
            except WebDriverException:
                pass
    driver.switch_to.window(original_window)

    # Remove duplicates while preserving order
    seen = set()
    unique_links = []
    for link in all_links:
        if link not in seen:
            seen.add(link)
            unique_links.append(link)

    logger.info(f"Total unique job links collected: {len(unique_links)}")
    return unique_links


def click_apply_button(driver, link):
    """
    Try to click the Apply button on a job detail page.
    Updated: Naukri.com now uses "Apply on company site" button text.

    Returns True if applied successfully, False otherwise.
    """
    # First, wait for the page to load
    time.sleep(4)

    # Try to find and click the apply button (updated selector)
    # New: button text is "Apply on company site", id="company-site-button"
    apply_selectors = [
        (By.XPATH, "//button[contains(text(),'Apply on company site')]"),
        (By.XPATH, "//button[contains(text(),'Apply')]"),
        (By.ID, "company-site-button"),
        (By.CSS_SELECTOR, "button[class*='company-site-button']"),
        (By.CSS_SELECTOR, "[class*='apply-button-container'] button"),
    ]

    for by, selector in apply_selectors:
        try:
            apply_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, selector))
            )
            apply_btn.click()
            return True
        except (TimeoutException, NoSuchElementException):
            continue

    return False


def apply_to_jobs(driver, job_links):
    """
    Visit each job link and attempt to apply.
    Returns (applied_count, failed_count, applied_list).
    """
    applied = 0
    failed = 0
    applied_list = {'passed': [], 'failed': []}

    logger.info("=" * 50)
    logger.info(f"Starting job applications (max: {MAX_APPLICATIONS})...")
    logger.info("=" * 50)

    for i, link in enumerate(job_links, 1):
        if applied >= MAX_APPLICATIONS:
            logger.info(f"Reached max application limit ({MAX_APPLICATIONS}). Stopping.")
            break

        logger.info(f"[{i}/{len(job_links)}] Visiting job: {link}")
        try:
            driver.get(link)
        except WebDriverException as e:
            logger.warning(f"  ✗ Failed to load page: {e}")
            failed += 1
            applied_list['failed'].append(link)
            continue

        # --- Click the "Apply" button ---
        if click_apply_button(driver, link):
            applied += 1
            applied_list['passed'].append(link)
            logger.info(f"  ✓ Applied! Total: {applied}")
            time.sleep(2)
        else:
            failed += 1
            applied_list['failed'].append(link)
            logger.warning(f"  ✗ No Apply button found. Fail count: {failed}")
            continue

        # --- Handle any additional fields (first/last name, submit) ---
        # Note: These handlers remain from the original script but the new Naukri
        # uses "Apply on company site" which typically redirects to an external site.
        # If Naukri's inline application form appears, these will handle it.
        try:
            # Check for daily quota expired
            quota_el = driver.find_element(By.XPATH, "//*[text()='Your daily quota has been expired.']")
            if quota_el:
                logger.info("Daily quota expired. Stopping.")
                break
        except NoSuchElementException:
            pass

        try:
            # If first name field is present, fill it
            driver.find_element(By.XPATH, "//input[@id='CUSTOM-FIRSTNAME']")
            firstname_el = driver.find_element(By.ID, 'CUSTOM-FIRSTNAME')
            firstname_el.clear()
            firstname_el.send_keys(FIRSTNAME)
            logger.info("  Filled custom first name field")
        except NoSuchElementException:
            pass

        try:
            # If last name field is present, fill it
            driver.find_element(By.XPATH, "//input[@id='CUSTOM-LASTNAME']")
            lastname_el = driver.find_element(By.ID, 'CUSTOM-LASTNAME')
            lastname_el.clear()
            lastname_el.send_keys(LASTNAME)
            logger.info("  Filled custom last name field")
        except NoSuchElementException:
            pass

        try:
            # Click "Submit and Apply" if present
            submit_btn = driver.find_element(By.XPATH, "//*[text()='Submit and Apply']")
            submit_btn.click()
            logger.info("  Clicked 'Submit and Apply'")
            time.sleep(2)
        except NoSuchElementException:
            pass

    return applied, failed, applied_list


def save_results(applied_list):
    """Save applied/failed links to CSV."""
    csv_file = "naukriapplied.csv"
    final_dict = {k: pd.Series(v) for k, v in applied_list.items()}
    df = pd.DataFrame.from_dict(final_dict)
    df.to_csv(csv_file, index=False)
    logger.info(f"Results saved to {csv_file}")


def main():
    """Main entry point for the bot."""
    logger.info("=" * 50)
    logger.info("Naukri Auto-Apply Bot (Edge Edition - 2026 Update)")
    logger.info("=" * 50)

    if not validate_config():
        return

    logger.info(f"Keywords: {KEYWORDS}")
    logger.info(f"Location: {LOCATION or 'Anywhere'}")
    logger.info(f"Max applications: {MAX_APPLICATIONS}")
    logger.info(f"Pages per keyword: {PAGES_PER_KEYWORD}")

    driver = None
    try:
        # Initialize browser
        driver = create_edge_driver()

        # Login
        login_naukri(driver)

        # Build all search URLs
        search_urls = build_search_urls()
        logger.info(f"Total search pages to open: {len(search_urls)}")

        # Collect all job links in parallel (multi-tab)
        job_links = collect_all_jobs_parallel(driver, search_urls)

        if not job_links:
            logger.warning("No job links found. Check your keywords and location.")
            return

        # Apply to jobs
        applied, failed, applied_list = apply_to_jobs(driver, job_links)

        # Save results
        save_results(applied_list)

        # Summary
        logger.info("=" * 50)
        logger.info("APPLICATION SUMMARY")
        logger.info(f"  Successfully applied: {applied}")
        logger.info(f"  Failed/Skipped:      {failed}")
        logger.info(f"  Total processed:     {applied + failed}")
        logger.info(f"  Results saved to:    naukriapplied.csv")
        logger.info("=" * 50)

    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                logger.info("Browser closed.")
            except Exception:
                pass


if __name__ == "__main__":
    main()