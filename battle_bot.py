import json
import random
import urllib.parse
import requests
import re
import sys
import time
import asyncio
import os
from playwright.async_api import async_playwright

# Ensure stdout/stderr handles Unicode characters on Windows
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
def load_topics_list():
    try:
        with open('topics.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[-] Error reading topics.json: {e}")
        sys.exit(1)

TOPICS_LIST = load_topics_list()

def load_env():
    env = {
        'tgbotapi': os.environ.get('TELEGRAM_BOT_TOKEN'),
        'chatid': os.environ.get('TELEGRAM_CHAT_ID')
    }
    # Fallback to .env file if not present in env
    if not env['tgbotapi'] or not env['chatid']:
        try:
            if os.path.exists('.env'):
                with open('.env', 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith('tgbotapi:'):
                            env['tgbotapi'] = line[len('tgbotapi:'):].strip()
                        elif line.startswith('chatid:'):
                            env['chatid'] = line[len('chatid:'):].strip()
        except Exception as e:
            print(f"[-] Warning: Failed to load .env: {e}")
    return env

def send_telegram_report(env, topic_name, battle_url, answered_count, total_questions, screenshot_path):
    token = env.get('tgbotapi')
    chat_id = env.get('chatid')
    if not token or not chat_id:
        print("[-] Telegram credentials missing in .env. Skipping report.")
        return
    
    caption = (
        f"🏆 *Chorcha Battle Report* 🏆\n\n"
        f"📖 *Topic:* {topic_name}\n"
        f"🔗 [Battle URL]({battle_url})\n"
        f"✅ *Questions Answered:* {answered_count}/{total_questions}\n\n"
        f"🤖 _Automated by Chorcha Bot_"
    )
    
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with open(screenshot_path, 'rb') as photo_file:
            files = {'photo': photo_file}
            data = {
                'chat_id': chat_id,
                'caption': caption,
                'parse_mode': 'Markdown'
            }
            res = requests.post(url, data=data, files=files)
            if res.status_code == 200:
                print("[+] Telegram report sent successfully.")
            else:
                print(f"[-] Failed to send Telegram report: HTTP {res.status_code} - {res.text}")
    except Exception as e:
        print(f"[-] Error sending Telegram report: {e}")

def decode_value(encoded_str, key):
    if not key:
        return encoded_str
    decoded = []
    key_len = len(key)
    for i, char in enumerate(encoded_str):
        cp = ord(char)
        kc = ord(key[i % key_len])
        decoded.append(chr((cp - kc + 65536) % 65536))
    return "".join(decoded)

def decode_object(obj, key):
    if isinstance(obj, str):
        return decode_value(obj, key)
    elif isinstance(obj, list):
        return [decode_object(item, key) for item in obj]
    elif isinstance(obj, dict):
        return {k: decode_object(v, key) for k, v in obj.items()}
    return obj

def load_cookies_for_requests():
    try:
        cookie_env = os.environ.get('COOKIE_JSON')
        if cookie_env:
            return json.loads(cookie_env)
        with open('cookie.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[-] Error reading cookie.json: {e}")
        sys.exit(1)

def format_cookies_for_playwright(cookies_list):
    formatted = []
    for c in cookies_list:
        cookie = {
            'name': c['name'],
            'value': c['value'],
            'domain': c['domain'],
            'path': c['path']
        }
        if 'secure' in c:
            cookie['secure'] = c['secure']
        if 'httpOnly' in c:
            cookie['httpOnly'] = c['httpOnly']
        if 'sameSite' in c and c['sameSite'] is not None:
            same_site = str(c['sameSite']).capitalize()
            if same_site in ["Strict", "Lax", "None"]:
                cookie['sameSite'] = same_site
        if 'expirationDate' in c:
            cookie['expires'] = int(c['expirationDate'])
        formatted.append(cookie)
    return formatted

def create_battle_rooms(session, count):
    urls = []
    print(f"[*] Starting creation of {count} battle room(s)...")
    for i in range(1, count + 1):
        topic = random.choice(TOPICS_LIST)
        topic_id = topic['TOPIC_ID']
        topic_name = topic['TOPIC_NAME']

        print(f"[{i}/{count}] Selecting topic: {topic_name} (ID: {topic_id})")

        # Step 1: Quick Exam API
        quick_url = "https://mujib.chorcha.net/exam/quick"
        try:
            res = session.post(quick_url, json={"topics": [topic_id], "type": "BATTLE"}, headers={"Content-Type": "application/json"})
            if res.status_code != 200:
                print(f"    [-] Quick Exam API failed with status {res.status_code}")
                continue
            druto_id = res.json().get('data', {}).get('druto_id')
            if not druto_id:
                print(f"    [-] druto_id not found in response: {res.text}")
                continue
            
            # Step 2: Battle Create API
            create_url = "https://mujib.chorcha.net/battle/create"
            res = session.post(create_url, json={
                "druto_id": druto_id,
                "topic_id": topic_id,
                "challenge_type": "friends",
                "topic_name": topic_name
            }, headers={"Content-Type": "application/json"})
            
            if res.status_code != 200:
                print(f"    [-] Battle Create API failed with status {res.status_code}")
                continue
            
            room_id = res.json().get('data', {}).get('room_id')
            if not room_id:
                print(f"    [-] room_id not found in response: {res.text}")
                continue

            battle_url = f"https://chorcha.net/battle/{room_id}?topic={urllib.parse.quote(topic_name)}"
            print(f"    [+] Created battle room: {battle_url}")
            urls.append(battle_url)
            
            # Delay to avoid rate limiting
            time.sleep(2)
        except Exception as e:
            print(f"    [-] Exception creating battle room: {e}")
    return urls

def fetch_and_decode_answers(session, druto_id):
    config_url = "https://mujib.chorcha.net/battle/exam-config"
    headers = {
        'Content-Type': 'application/json'
    }
    try:
        res = session.post(config_url, json={"druto_id": druto_id}, headers=headers)
        if res.status_code != 200:
            print(f"[-] Failed to fetch battle answers config: HTTP {res.status_code}")
            return None
        
        data = res.json()
        
        # Extract questions from raw plaintext response
        questions = (
            data.get('data', {}).get('questions') or 
            data.get('data', {}).get('exam_questions') or 
            data.get('questions') or 
            []
        )
        
        answers_map = {}
        for idx, q in enumerate(questions):
            ans_val = q.get('answer')
            correct_idx = q.get('correct_answer')
            
            if correct_idx is not None:
                answers_map[idx + 1] = int(correct_idx)
            elif ans_val is not None:
                ans_str = str(ans_val).upper().strip()
                mapping = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
                if ans_str in mapping:
                    answers_map[idx + 1] = mapping[ans_str]
                else:
                    try:
                        answers_map[idx + 1] = int(ans_str)
                    except ValueError:
                        answers_map[idx + 1] = ans_str
        
        return answers_map
    except Exception as e:
        print(f"[-] Exception fetching answers: {e}")
        return None

async def play_battle(context, url_idx, url, session):
    print(f"\n========================================")
    print(f"[*] [{url_idx}] Starting Battle URL: {url}")
    print(f"========================================")
    
    # Extract druto_id
    match = re.search(r'BATTLE_[a-zA-Z0-9_\-]{16}', url)
    if not match:
        print(f"[-] [{url_idx}] Could not extract druto_id from URL. Skipping.")
        return
    druto_id = match.group(0)
    
    # Fetch answers (non-blocking via executor)
    loop = asyncio.get_event_loop()
    answers_map = await loop.run_in_executor(None, fetch_and_decode_answers, session, druto_id)
    if not answers_map:
        print(f"[-] [{url_idx}] Could not fetch answers. Skipping.")
        return
    print(f"[+] [{url_idx}] Loaded {len(answers_map)} answers.")
    
    page = await context.new_page()
    await page.goto(url)
    await page.wait_for_load_state("networkidle")
    
    # Click "ব্যাটেল শুরু করো"
    try:
        start_btn = page.locator("button:has-text('ব্যাটেল শুরু করো')")
        await start_btn.wait_for(state="visible", timeout=10000)
        await start_btn.click()
        print(f"[+] [{url_idx}] Clicked 'ব্যাটেল শুরু করো'")
    except Exception as e:
        print(f"[-] [{url_idx}] Start button not found or click failed (maybe already started): {e}")
    
    # Wait for battle to start (when 4 non-empty option buttons appear)
    print(f"[*] [{url_idx}] Waiting for opponent to join and battle to start...")
    start_wait_time = time.time()
    last_log_time = time.time()
    
    while True:
        try:
            # Find all buttons
            buttons = await page.locator("button.custom-scrollbar, button.flex.w-full.gap-2.rounded-lg").all()
            non_empty_buttons = []
            for btn in buttons:
                try:
                    txt = (await btn.inner_text()).strip()
                    if txt:
                        non_empty_buttons.append(btn)
                except Exception:
                    pass
            
            if len(non_empty_buttons) >= 4:
                print(f"[+] [{url_idx}] Opponent joined! Battle started after {int(time.time() - start_wait_time)}s.")
                break
        except Exception as e:
            print(f"[-] [{url_idx}] Error checking battle start status: {e}")
            break
        
        # Log status every 5 seconds
        if time.time() - last_log_time >= 5:
            elapsed = int(time.time() - start_wait_time)
            print(f"[*] [{url_idx}] Still waiting for opponent... ({elapsed}s elapsed)")
            last_log_time = time.time()
        
        await page.wait_for_timeout(1000)
    
    # Answering loop
    last_signature = ""
    answered_count = 0
    total_questions = len(answers_map)
    consecutive_misses = 0
    
    while answered_count < total_questions:
        # Find current options
        buttons = await page.locator("button.custom-scrollbar, button.flex.w-full.gap-2.rounded-lg").all()
        if len(buttons) < 4:
            await page.wait_for_timeout(500)
            consecutive_misses += 1
            if consecutive_misses > 120: # 60 seconds of no options during active battle
                print(f"[-] [{url_idx}] Timeout waiting for question options. Ending battle loop.")
                break
            continue
        
        consecutive_misses = 0
        
        # Check if already answered (highlighted options)
        has_been_answered = False
        for btn in buttons:
            class_name = await btn.get_attribute("class") or ""
            if any(highlight in class_name for highlight in [
                'bg-[#1899181a]', 'bg-[#1899181A]', 'border-[#189918]', 
                'bg-[#FFF1F1]', 'border-[#AF5454]', 'bg-[#ef444430]', 'bg-[#EF444430]'
            ]):
                has_been_answered = True
                break
        
        if has_been_answered:
            await page.wait_for_timeout(500)
            continue
        
        # Build question signature
        options_texts = []
        for btn in buttons:
            try:
                options_texts.append((await btn.inner_text()).strip())
            except Exception:
                options_texts.append("")
        
        # Retrieve question text via page.evaluate
        try:
            question_text = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button.custom-scrollbar, button.flex.w-full.gap-2.rounded-lg')).filter(b => b.innerText.trim() !== "");
                if (btns.length === 0) return "";
                const firstBtn = btns[0];
                const parent = firstBtn.parentElement;
                if (parent) {
                    const prev = parent.previousElementSibling;
                    if (prev && prev.innerText.trim()) return prev.innerText.trim();
                    const grandparent = parent.parentElement;
                    if (grandparent) {
                        const gpPrev = grandparent.previousElementSibling;
                        if (gpPrev && gpPrev.innerText.trim()) return gpPrev.innerText.trim();
                    }
                }
                return "";
            }""")
        except Exception:
            question_text = ""
        
        current_signature = f"{question_text}||{'|'.join(options_texts)}"
        if current_signature == last_signature:
            # Still waiting for a new question transition
            await page.wait_for_timeout(200)
            continue
        
        # Answer question
        q_num = answered_count + 1
        correct_idx = answers_map.get(q_num)
        
        if correct_idx is None:
            print(f"[-] [{url_idx}] No answer mapped for Q{q_num}. Choosing default index 0.")
            correct_idx = 0
        
        if isinstance(correct_idx, str):
            mapping = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
            correct_idx = mapping.get(correct_idx.upper(), 0)
        
        print(f"[+] [{url_idx}] Q{q_num}/{total_questions}: Answering with Option {correct_idx + 1}...")
        
        try:
            await buttons[correct_idx].click()
            last_signature = current_signature
            answered_count += 1
        except Exception as e:
            print(f"[-] [{url_idx}] Failed to click option button: {e}")
            await page.wait_for_timeout(500)
    
    print(f"[+] [{url_idx}] All questions answered. Waiting 3.5 seconds to load scoreboard...")
    await page.wait_for_timeout(6000)
    
    # Take screenshot
    screenshot_path = f"battle_result_{url_idx}.png"
    try:
        await page.screenshot(path=screenshot_path)
        print(f"[+] [{url_idx}] Screenshot captured: {screenshot_path}")
    except Exception as e:
        print(f"[-] [{url_idx}] Failed to capture screenshot: {e}")
        screenshot_path = None
    
    # Send telegram report
    if screenshot_path:
        env = await loop.run_in_executor(None, load_env)
        # Parse topic name from URL or config
        topic_name = "Unknown Topic"
        match_topic = re.search(r'topic=([^&]+)', url)
        if match_topic:
            topic_name = urllib.parse.unquote(match_topic.group(1))
        
        await loop.run_in_executor(
            None,
            send_telegram_report,
            env,
            topic_name,
            url,
            answered_count,
            total_questions,
            screenshot_path
        )
    
    await page.close()
    print(f"[+] [{url_idx}] Finished Battle.")

async def run_battle_automation(urls, cookies_list, session):
    formatted_cookies = format_cookies_for_playwright(cookies_list)
    
    async with async_playwright() as p:
        # Run headless dynamically if in a CI environment (like GitHub Actions)
        is_headless = os.environ.get('GITHUB_ACTIONS') == 'true' or os.environ.get('HEADLESS') == 'true'
        browser = await p.chromium.launch(headless=is_headless)
        context = await browser.new_context()
        await context.add_cookies(formatted_cookies)
        
        print("[*] Playwright browser launched and cookies injected.")
        
        # Start all battles in parallel
        tasks = []
        for url_idx, url in enumerate(urls, 1):
            tasks.append(play_battle(context, url_idx, url, session))
        
        await asyncio.gather(*tasks)
        await context.close()
        await browser.close()
    print("\n[*] All automation runs completed successfully!")

def main():
    # Load cookies
    cookies_list = load_cookies_for_requests()
    
    # Initialize request session
    session = requests.Session()
    for cookie in cookies_list:
        session.cookies.set(
            name=cookie['name'],
            value=cookie['value'],
            domain=cookie.get('domain', '.chorcha.net'),
            path=cookie.get('path', '/')
        )
    
    # Check command-line arguments first, fallback to default or prompt
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        count = int(sys.argv[1])
    else:
        try:
            count_input = input("How many battle rooms do you want to create? (default 5): ").strip()
            if count_input.isdigit():
                count = int(count_input)
            else:
                count = 5
        except (IOError, EOFError):
            print("[*] Non-interactive environment detected. Defaulting to 1 room.")
            count = 1
        
    # Step 1 & 2: Create battle rooms
    urls = create_battle_rooms(session, count)
    if not urls:
        print("[-] No battle rooms were created. Exiting.")
        return
        
    # Run Playwright automation to solve the battles
    asyncio.run(run_battle_automation(urls, cookies_list, session))

if __name__ == "__main__":
    main()
