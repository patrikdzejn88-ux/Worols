from __future__ import annotations

import argparse
import json
import os
import random
import re
import string
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from colorama import Fore, Style, init
from curl_cffi import requests as cr
from temptf import Message, TempTFClient

init(autoreset=True)

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
PROXIES_TXT = ROOT / "proxies.txt"
ACCOUNTS = LOGS / "accounts.json"

API = "https://api.worols.com"
WEB = "https://play.worols.com"
UA = "Mozilla/5.0 (Linux; Android 14; 22081212C Build/UQ1A.240205.07131809; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.82 Mobile Safari/537.36"

MAX_ATTEMPTS = 3
MAIL_TIMEOUT = 120
DEFAULT_WORKERS = 3

MAIL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("all", "all providers (gmail + high.edu.pl)"),
    ("gmail", "gmail.com (dot + plus)"),
    ("gmail-dot", "gmail.com (dot only)"),
    ("gmail-plus", "gmail.com (plus only)"),
    ("high.edu.pl", "high.edu.pl (edu)"),
)
MAIL_PROVIDERS = tuple(k for k, _ in MAIL_OPTIONS)

BANNER = f"""
{Fore.MAGENTA}╦ ╦╔═╗╦═╗╔═╗╦  ╔═╗
{Fore.MAGENTA}║║║║ ║╠╦╝║ ║║  ╚═╗
{Fore.WHITE}╚╩╝╚═╝╩╚═╚═╝╩═╝╚═╝{Style.RESET_ALL}
"""

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _vlen(s: object) -> int:
    return len(_ANSI_RE.sub("", str(s)))


def _panel(title: str, lines: list[tuple[str, str]], col=Fore.CYAN, w: int = 40) -> None:
    inner = w - 4
    bar = "─" * max(w - len(title) - 7, 1)
    print(f" {col}┌── {title} {bar}┐{Style.RESET_ALL}")
    for lbl, val in lines:
        gap = max(inner - _vlen(lbl) - _vlen(val), 1)
        print(f" {col}│{Style.RESET_ALL} {lbl}{' ' * gap}{val} {col}│{Style.RESET_ALL}")
    print(f" {col}└{'─' * (w - 2)}┘{Style.RESET_ALL}")


_FIRST = ("ivan", "alex", "dmitry", "sergey", "andrey", "nikita", "max", "artem", "misha", "vlad",
          "pavel", "roman", "igor", "oleg", "anton", "denis", "kirill", "vadim", "egor", "timur",
          "maria", "anna", "olga", "katya", "dasha", "natasha", "irina", "sveta", "lena", "polina",
          "john", "mike", "chris", "david", "james", "mark", "paul", "sam", "tom", "ben",
          "nick", "dan", "julia", "kate", "emma", "olivia", "sophie", "lucas", "leo", "noah")
_LAST = ("smirnov", "ivanov", "kuznetsov", "popov", "sokolov", "lebedev", "kozlov", "novikov",
         "morozov", "petrov", "volkov", "solovyov", "vasilyev", "zaitsev", "pavlov", "semenov",
         "smith", "johnson", "brown", "taylor", "miller", "wilson", "moore", "white", "hall",
         "young", "king", "scott", "green", "baker", "adams", "nelson", "carter")

_USERNAME_PATTERNS = (
    lambda f, l: f"{f}{l}{random.randint(10, 999)}",
    lambda f, l: f"{f}.{l}{random.randint(10, 999)}",
    lambda f, l: f"{f}{l}{random.randint(1000, 99999)}",
    lambda f, l: f"{f}{l}{random.randint(1, 999)}",
    lambda f, l: f"{f}.{l}{random.randint(100, 9999)}",
    lambda f, l: f"{f}{l[0]}{random.randint(100, 9999)}",
    lambda f, l: f"{f}{l}{random.randint(1985, 2004)}",
)

_REGISTRATION_Q = """mutation registration($login: String!, $email: String!, $password: String!, $invite: String, $recaptcha_token: String, $user_agent: String, $related_user_ids: [ID], $fingerprint: String) {
  registration(login: $login, email: $email, password: $password, invite: $invite, recaptcha_token: $recaptcha_token, user_agent: $user_agent, related_user_ids: $related_user_ids, fingerprint: $fingerprint) {
    token
    user { id login email nickname status }
  }
}"""

_AUTH_Q = """mutation auth($login: String!, $password: String!, $recaptcha_token: String, $push_token: String) {
  auth(login: $login, password: $password, recaptcha_token: $recaptcha_token, push_token: $push_token) {
    token
    user { id login email status }
  }
}"""


class Retry(Exception):
    pass


_print_lock = threading.Lock()
_save_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val if val else default


def _ask_int(prompt: str, default: int) -> int:
    try:
        return max(1, int(_ask(prompt, str(default))))
    except ValueError:
        return default


def _ask_providers(raw_arg: str = "") -> list[str]:
    if raw_arg:
        chosen = [p.strip().lower() for p in raw_arg.split(",") if p.strip()]
        valid = [p for p in chosen if p in MAIL_PROVIDERS]
        if valid:
            return ["all"] if "all" in valid else valid

    print(f"  {Fore.CYAN}mail providers:{Style.RESET_ALL}")
    for i, (key, desc) in enumerate(MAIL_OPTIONS, 1):
        print(f"    {Fore.CYAN}{i}{Style.RESET_ALL}) {key:<12} {Fore.WHITE}{desc}{Style.RESET_ALL}")
    raw = _ask("select (e.g. 1 or 2,5)", "1")

    selected: list[str] = []
    for part in raw.replace(" ", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(MAIL_OPTIONS):
                key = MAIL_OPTIONS[idx - 1][0]
                if key not in selected:
                    selected.append(key)
        elif part in MAIL_PROVIDERS and part not in selected:
            selected.append(part)

    if not selected or "all" in selected:
        return ["all"]
    return selected


def gen_username() -> str:
    return random.choice(_USERNAME_PATTERNS)(random.choice(_FIRST), random.choice(_LAST))


def gen_password() -> str:
    parts = (
        random.choices(string.ascii_lowercase, k=4)
        + random.choices(string.ascii_uppercase, k=3)
        + random.choices(string.digits, k=3)
        + random.choices("_!", k=2)
    )
    random.shuffle(parts)
    return "".join(parts)


def load_proxies() -> list[dict]:
    if not PROXIES_TXT.exists():
        return []
    out = []
    for line in PROXIES_TXT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            u = urlparse(line)
            if not (u.hostname and u.port):
                continue
            out.append({
                "server": f"{u.scheme}://{u.hostname}:{u.port}",
                "username": u.username or "",
                "password": u.password or "",
            })
        else:
            parts = line.split(":", 3)
            if len(parts) == 4:
                host, port, user, pw = parts
                out.append({"server": f"http://{host}:{port}", "username": user, "password": pw})
            elif len(parts) == 2:
                host, port = parts
                out.append({"server": f"http://{host}:{port}", "username": "", "password": ""})
    return out


def proxy_for(proxies: list[dict], sess: str) -> dict | None:
    if not proxies:
        return None
    p = dict(random.choice(proxies))
    if p.get("username"):
        p["username"] = f"{p['username']}-sessid-{sess}-sesstime-15"
    return p


def proxy_url(p: dict | None) -> str | None:
    if not p:
        return None
    scheme, host = p["server"].split("://", 1)
    auth = f"{p['username']}:{p['password']}@" if p.get("username") else ""
    return f"{scheme}://{auth}{host}"


def load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        bak = path.with_name(path.name + ".bak")
        try:
            path.replace(bak)
            log(f"  {Fore.RED}{path.name} битый — бэкап в {bak.name}{Style.RESET_ALL}")
        except Exception:
            pass
        return []


def save_account(row: dict) -> None:
    with _save_lock:
        rows = load_json(ACCOUNTS)
        rows.append(row)
        tmp = ACCOUNTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, ACCOUNTS)


class TempMail:
    def __init__(self, providers: list[str] | None = None, proxy: str | None = None):
        opener = None
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        self.client = TempTFClient(timeout=20.0, opener=opener)
        self.providers = providers or ["all"]
        self.address = ""

    def create(self) -> str:
        choice = random.choice(self.providers)
        for attempt in range(3):
            try:
                if choice == "gmail":
                    self.address = self.client.create_gmail()
                elif choice == "gmail-dot":
                    self.address = self.client.create_gmail_dot()
                elif choice == "gmail-plus":
                    self.address = self.client.create_gmail_plus()
                elif choice == "high.edu.pl":
                    self.address = self.client.create_edu()
                else:
                    self.address = self.client.create_email(
                        providers=("gmail", "high.edu.pl"),
                        dot=True,
                        plus=True,
                    )
                return self.address
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 + attempt * 2)
        return ""

    @staticmethod
    def _extract_link(msg: Message) -> str | None:
        if "worols" not in f"{msg.sender} {msg.subject}".lower():
            return None
        raw = f"{msg.text} {msg.body}".replace("&amp;", "&")
        m = re.search(r"https?://[^\s\"'<>\\]*activate_account\?[^\s\"'<>\\]+", raw)
        if m:
            return m.group(0)
        m = re.search(r"activate_account\?token=([\w\-.=]+)", raw)
        if m:
            return f"https://worols.com/activate_account?token={m.group(1)}"
        return None

    def wait_activation_link(self, timeout: float = MAIL_TIMEOUT) -> str | None:
        try:
            msg = self.client.wait_for_message(
                self.address,
                timeout=timeout,
                interval=4.0,
                predicate=lambda m: self._extract_link(m) is not None,
            )
            return self._extract_link(msg)
        except TimeoutError:
            return None


class Worols:
    def __init__(self, proxy: str | None = None):
        self.s = cr.Session(impersonate="chrome")
        if proxy:
            self.s.proxies.update({"http": proxy, "https": proxy})
        self.s.headers.update({
            "user-agent": UA,
            "accept": "*/*",
            "content-type": "application/json",
            "origin": WEB,
            "referer": f"{WEB}/",
            "x-requested-with": "com.worols.play",
        })

    def _csrf(self) -> None:
        r = self.s.get(f"{API}/sanctum/csrf-cookie", timeout=30)
        r.raise_for_status()
        xsrf = self.s.cookies.get("XSRF-TOKEN") or ""
        if not xsrf:
            raise Retry("no xsrf cookie")
        self.s.headers["x-xsrf-token"] = unquote(xsrf)

    def _gql(self, operation: str, query: str, variables: dict) -> dict:
        payload = {"operationName": operation, "variables": variables, "query": query}
        if "x-xsrf-token" not in self.s.headers:
            self._csrf()
        for attempt in range(3):
            try:
                r = self.s.post(f"{API}/graphql", json=payload, timeout=30)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 + attempt * 2)
                continue
            if r.status_code == 419:
                self._csrf()
                continue
            if r.status_code >= 500 and attempt < 2:
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                raise RuntimeError(str(data["errors"][0].get("message", data["errors"]))[:200])
            return data
        raise Retry("gql retry exhausted")

    def registration(self, login: str, email: str, password: str) -> dict:
        data = self._gql("registration", _REGISTRATION_Q, {
            "login": login,
            "email": email,
            "password": password,
            "user_agent": UA,
        })
        reg = (data.get("data") or {}).get("registration")
        if not reg or not reg.get("user"):
            raise RuntimeError(f"registration: {str(data)[:200]}")
        return reg

    def auth(self, login: str, password: str) -> dict:
        data = self._gql("auth", _AUTH_Q, {"login": login, "password": password})
        return (data.get("data") or {}).get("auth") or {}

    def activate(self, link: str) -> bool:
        for attempt in range(3):
            try:
                r = self.s.get(link, timeout=30, allow_redirects=False)
                return r.status_code in (200, 302)
            except Exception:
                if attempt == 2:
                    return False
                time.sleep(2 + attempt * 2)
        return False


def reg_one(idx: int, total: int, proxies: list[dict], providers: list[str]) -> bool:
    tag = f"{Fore.WHITE}[{idx}/{total}]{Style.RESET_ALL}"
    sess = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    purl = proxy_url(proxy_for(proxies, sess))

    mail = TempMail(providers=providers, proxy=purl)
    email = mail.create()
    login = re.sub(r"[^a-z0-9]", "", gen_username().lower())[:20]
    password = gen_password()
    log(f"  {tag}  {Fore.CYAN}···{Style.RESET_ALL}  {login}  {email}")

    client = Worols(proxy=purl)
    reg = client.registration(login, email, password)
    user = reg["user"]
    log(f"  {tag}  reg ok  id={user['id']}  status={user['status']}")

    row = {
        "ts": int(time.time()),
        "registered_at": datetime.now().isoformat(timespec="seconds"),
        "id": user["id"],
        "login": user["login"],
        "nickname": user.get("nickname", ""),
        "email": email,
        "password": password,
        "token": "",
        "status": "pending_activation",
    }

    link = mail.wait_activation_link()
    if not link:
        save_account(row)
        log(f"  {tag}  {Fore.RED}fail{Style.RESET_ALL}  no activation mail (акк сохранен)")
        return False
    if not client.activate(link):
        save_account(row)
        log(f"  {tag}  {Fore.RED}fail{Style.RESET_ALL}  activate link dead (акк сохранен)")
        return False

    try:
        auth = client.auth(login, password)
        row["token"] = auth.get("token") or ""
        row["status"] = (auth.get("user") or {}).get("status") or "activated"
    except Exception as e:
        row["status"] = "activated"
        log(f"  {tag}  {Fore.YELLOW}auth warn: {str(e)[:80]}{Style.RESET_ALL}")

    save_account(row)
    log(f"  {tag}  {Fore.GREEN}ok{Style.RESET_ALL}  {user['login']}  {email}  status={row['status']}")
    return True


def reg_account(idx: int, total: int, proxies: list[dict], providers: list[str]) -> bool:
    tag = f"{Fore.WHITE}[{idx}/{total}]{Style.RESET_ALL}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            log(f"  {tag}  {Fore.YELLOW}попытка {attempt}/{MAX_ATTEMPTS}{Style.RESET_ALL}")
        try:
            if reg_one(idx, total, proxies, providers):
                return True
        except Retry as e:
            log(f"  {tag}  {Fore.YELLOW}retry: {e}{Style.RESET_ALL}")
        except Exception as e:
            log(f"  {tag}  {Fore.RED}error: {str(e)[:120]}{Style.RESET_ALL}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2 * attempt, 10))
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--provider", type=str, default="")
    args = ap.parse_args()

    print(BANNER)
    proxies = load_proxies()
    count = args.count if args.count > 0 else _ask_int("accounts", 1)
    workers = args.workers if args.workers > 0 else _ask_int("threads", DEFAULT_WORKERS)
    workers = max(1, min(workers, count))
    providers = _ask_providers(args.provider)
    mail_label = ",".join(providers)

    t0 = time.time()
    print()
    _panel(
        "config",
        [
            ("accounts", f"{Fore.CYAN}{count}{Style.RESET_ALL}"),
            ("threads", f"{Fore.CYAN}{workers}{Style.RESET_ALL}"),
            ("mail", f"{Fore.CYAN}{mail_label}{Style.RESET_ALL}"),
            ("proxies", str(len(proxies)) if proxies else "off"),
            ("saved", str(len(load_json(ACCOUNTS)))),
        ],
    )
    print()

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(reg_account, i, count, proxies, providers) for i in range(1, count + 1)]
        for fut in as_completed(futs):
            if fut.result():
                ok += 1
            else:
                fail += 1

    print()
    _panel(
        "done",
        [
            ("ok", f"{Fore.GREEN}{ok}{Style.RESET_ALL}"),
            ("fail", f"{Fore.RED}{fail}{Style.RESET_ALL}"),
            ("file", "logs/accounts.json"),
            ("time", f"{time.time() - t0:.1f}s"),
        ],
    )
    print()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
