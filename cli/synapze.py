#!/usr/bin/env python3
"""
Synapze V2 — Terminal CLI
"""
import asyncio
import json
import os
import readline
import signal
import sys
import uuid
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()
CONFIG_DIR = Path.home() / ".synapze"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / ".history"

BANNER = r"""[bold cyan]
 ███████╗██╗   ██╗███╗  ██╗ █████╗ ██████╗ ███████╗███████╗
 ██╔════╝╚██╗ ██╔╝████╗ ██║██╔══██╗██╔══██╗╚══███╔╝██╔════╝
 ███████╗ ╚████╔╝ ██╔██╗██║███████║██████╔╝  ███╔╝ █████╗
 ╚════██║  ╚██╔╝  ██║╚████║██╔══██║██╔═══╝  ███╔╝  ██╔══╝
 ███████║   ██║   ██║ ╚███║██║  ██║██║     ███████╗███████╗
 ╚══════╝   ╚═╝   ╚═╝  ╚══╝╚═╝  ╚═╝╚═╝     ╚══════╝╚══════╝
[/bold cyan][dim]  v2 · email · calendar · whatsapp · slack · browser · memory[/dim]
"""

HELP = """
[bold]Commands[/bold]
  [cyan]/new[/cyan]               New conversation session
  [cyan]/history[/cyan]           Show current session history
  [cyan]/clear[/cyan]             Clear session memory
  [cyan]/status[/cyan]            Check server + integrations
  [cyan]/auth google[/cyan]       Connect Gmail + Calendar (OAuth)
  [cyan]/auth slack[/cyan]        Slack setup guide
  [cyan]/auth wa[/cyan]           WhatsApp (Twilio) setup guide
  [cyan]/memory <query>[/cyan]    Search your saved memories
  [cyan]/schedule[/cyan]          Schedule a future task
  [cyan]/stream on|off[/cyan]     Toggle streaming (default: on)
  [cyan]/set-url <url>[/cyan]     Set API server URL
  [cyan]/set-token <tok>[/cyan]   Set auth token
  [cyan]/config[/cyan]            Show config
  [cyan]/help[/cyan]              This help
  [cyan]/exit[/cyan]              Exit

[bold]Examples[/bold]
  [dim]> Read my unread emails and summarize the important ones[/dim]
  [dim]> Reply to Rahul saying I'll call him tomorrow at 11am[/dim]
  [dim]> Book a meeting with priya@example.com Friday 3pm for 1hr, add Meet link[/dim]
  [dim]> Go to canva.com and create a new presentation about OhhIdeaX[/dim]
  [dim]> WhatsApp +919876543210: "Running 10 mins late"[/dim]
  [dim]> Post in #general on Slack: "Server deploy done"[/dim]
  [dim]> Remember that Vikram prefers calls over emails[/dim]
  [dim]> What do I know about investor Anjali?[/dim]
"""


class CLI:
    def __init__(self):
        self.config = self._load_config()
        self.session_id = str(uuid.uuid4())
        self.streaming = True
        self.base_url = self.config.get("api_url", "http://localhost:8000")
        self.token = self.config.get("token", "")
        self._setup_readline()

    # ── Config ──────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        CONFIG_DIR.mkdir(exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"api_url": "http://localhost:8000", "token": ""}

    def _save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)  # owner read/write only

    def _setup_readline(self):
        if HISTORY_FILE.exists():
            try:
                readline.read_history_file(str(HISTORY_FILE))
            except Exception:
                pass
        readline.set_history_length(1000)

    def _save_history(self):
        try:
            readline.write_history_file(str(HISTORY_FILE))
        except Exception:
            pass

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def _require_auth(self) -> bool:
        if not self.token:
            console.print("[red]Not authenticated. Run: /auth google[/red]")
            return False
        return True

    # ── Display helpers ─────────────────────────────────────────────────────

    def _print_banner(self):
        console.print(BANNER)
        console.print(
            f"[dim]Session:[/dim] [cyan]{self.session_id[:8]}...[/cyan]  "
            f"[dim]API:[/dim] [cyan]{self.base_url}[/cyan]  "
            f"[dim]/help for commands[/dim]\n"
        )

    def _print_tool_start(self, tool: str, inp: dict):
        icons = {
            "gmail": "📧", "calendar": "📅", "whatsapp": "💬",
            "slack": "🔔", "browser": "🌐", "memory": "🧠",
        }
        icon = icons.get(tool.split("_")[0], "🔧")
        preview = json.dumps(inp, ensure_ascii=False)
        if len(preview) > 80:
            preview = preview[:77] + "..."
        console.print(f"  [dim]{icon} {tool}[/dim]  [dim italic]{preview}[/dim]")

    def _print_tool_result(self, tool: str, result: dict, success: bool):
        if not success or result.get("error"):
            console.print(f"  [red]  ✗ {result.get('error', 'failed')}[/red]")
        elif "emails" in result:
            console.print(f"  [green]  ✓ {result.get('count', 0)} emails[/green]")
        elif "events" in result:
            console.print(f"  [green]  ✓ {result.get('count', 0)} events[/green]")
        elif "messages" in result:
            console.print(f"  [green]  ✓ {result.get('count', 0)} messages[/green]")
        elif result.get("success"):
            console.print(f"  [green]  ✓ done[/green]")
        else:
            console.print(f"  [green]  ✓ ok[/green]")

    # ── Agent calls ─────────────────────────────────────────────────────────

    async def _stream(self, message: str):
        if not self._require_auth():
            return
        url = f"{self.base_url}/agent/stream"
        payload = {"message": message, "session_id": self.session_id}
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers
                ) as resp:
                    if resp.status_code == 401:
                        console.print("[red]Auth expired. Run /auth google[/red]")
                        return
                    if resp.status_code == 429:
                        console.print("[red]Rate limit hit. Wait a minute.[/red]")
                        return
                    if resp.status_code != 200:
                        body = await resp.aread()
                        console.print(f"[red]Server error {resp.status_code}: {body.decode()[:200]}[/red]")
                        return

                    console.print()
                    text_started = False

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        etype = event.get("type")

                        if etype == "text":
                            chunk = event.get("chunk", "")
                            if not text_started:
                                console.print("[bold cyan]Synapze[/bold cyan] ", end="")
                                text_started = True
                            print(chunk, end="", flush=True)

                        elif etype == "tool_start":
                            if text_started:
                                console.print()
                                text_started = False
                            self._print_tool_start(
                                event.get("tool", ""), event.get("input", {})
                            )

                        elif etype == "tool_result":
                            self._print_tool_result(
                                event.get("tool", ""),
                                event.get("result", {}),
                                event.get("success", True),
                            )

                        elif etype == "error":
                            console.print(f"\n[red]{event.get('message')}[/red]")

                        elif etype == "done":
                            if text_started:
                                console.print()
                            tools = event.get("tool_calls", 0)
                            tokens = event.get("tokens", 0)
                            if tools or tokens:
                                console.print(
                                    f"[dim]  {tools} tool call(s) · {tokens} tokens[/dim]"
                                )

        except httpx.ConnectError:
            console.print(
                f"[red]Cannot connect to {self.base_url}[/red]\n"
                "[dim]Start server: docker compose up  OR  uvicorn app.main:app --reload[/dim]"
            )
        except Exception as e:
            console.print(f"[red]Unexpected error: {e}[/red]")

    async def _chat(self, message: str):
        if not self._require_auth():
            return
        url = f"{self.base_url}/agent/chat"
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                with console.status("[cyan]Thinking...[/cyan]"):
                    resp = await client.post(
                        url,
                        json={"message": message, "session_id": self.session_id},
                        headers=self._headers,
                    )
            if resp.status_code == 401:
                console.print("[red]Auth expired. Run /auth google[/red]")
                return
            data = resp.json()
            tools = data.get("tool_calls", [])
            if tools:
                console.print(f"\n[dim]Tools:[/dim]", end=" ")
                for tc in tools:
                    status_icon = "✓" if tc.get("success") else "✗"
                    console.print(f"[dim]{status_icon} {tc['tool']}[/dim]", end="  ")
                console.print()
            console.print(f"\n[bold cyan]Synapze[/bold cyan]")
            console.print(Markdown(data.get("response", "")))
        except httpx.ConnectError:
            console.print(f"[red]Cannot connect to {self.base_url}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    # ── Commands ────────────────────────────────────────────────────────────

    async def _cmd_auth_google(self):
        url = f"{self.base_url}/auth/google"
        console.print(f"\n[cyan]Open in browser:[/cyan]  {url}\n")
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
        token = Prompt.ask("[cyan]Paste token here[/cyan]").strip()
        if token:
            self.token = token
            self.config["token"] = token
            self._save_config()
            console.print("[green]✓ Authenticated — Gmail + Calendar connected[/green]")
        else:
            console.print("[red]No token provided[/red]")

    async def _cmd_status(self):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                data = resp.json()
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Key", style="cyan", min_width=18)
            table.add_column("Value")
            table.add_row("Server", f"[green]✓ online[/green]  v{data.get('version', '?')}")
            table.add_row("Auth", "[green]✓ active[/green]" if self.token else "[red]✗ missing[/red]")
            table.add_row("Session", f"[dim]{self.session_id[:20]}...[/dim]")
            table.add_row("Streaming", "[green]on[/green]" if self.streaming else "[yellow]off[/yellow]")
            table.add_row("API URL", self.base_url)
            console.print()
            console.print(table)
        except httpx.ConnectError:
            console.print(f"[red]✗ Server offline at {self.base_url}[/red]")

    async def _cmd_history(self):
        if not self._require_auth():
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/agent/sessions/{self.session_id}/history",
                    headers=self._headers,
                )
            if resp.status_code != 200:
                console.print("[dim]No history yet.[/dim]")
                return
            history = resp.json().get("history", [])
            if not history:
                console.print("[dim]No messages in this session.[/dim]")
                return
            for msg in history:
                role = msg["role"]
                content = msg["content"][:300]
                if role == "user":
                    console.print(f"\n[bold]You[/bold]: {content}")
                else:
                    console.print(f"\n[bold cyan]Synapze[/bold cyan]: {content}")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    async def _cmd_clear(self):
        if not self._require_auth():
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(
                    f"{self.base_url}/agent/sessions/{self.session_id}",
                    headers=self._headers,
                )
        except Exception:
            pass
        self.session_id = str(uuid.uuid4())
        console.print(f"[green]✓ Cleared — new session: {self.session_id[:8]}...[/green]")

    async def _cmd_memory(self, query: str):
        if not self._require_auth():
            return
        if not query.strip():
            console.print("[dim]Usage: /memory <search query>[/dim]")
            return
        await self._stream(f"Search my memory for: {query}")

    async def _cmd_schedule(self):
        if not self._require_auth():
            return
        message = Prompt.ask("[cyan]What should I do?[/cyan]")
        run_at = Prompt.ask(
            "[cyan]When? (e.g. 2025-06-10T09:00:00+05:30 or Enter for now)[/cyan]",
            default="",
        )
        payload: dict = {"message": message, "session_id": self.session_id}
        if run_at.strip():
            payload["run_at"] = run_at.strip()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.base_url}/tasks/schedule",
                    json=payload,
                    headers=self._headers,
                )
            data = resp.json()
            console.print(f"[green]✓ Scheduled[/green]  ID: [dim]{data['task_id'][:16]}...[/dim]  When: {data.get('scheduled_for', 'now')}")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    def _cmd_config(self):
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="cyan", min_width=18)
        table.add_column("Value")
        tok = self.config.get("token", "")
        table.add_row("API URL", self.base_url)
        table.add_row("Token", (tok[:32] + "...") if tok else "[red]not set[/red]")
        table.add_row("Session", self.session_id[:20] + "...")
        table.add_row("Streaming", str(self.streaming))
        table.add_row("Config file", str(CONFIG_FILE))
        console.print()
        console.print(table)

    # ── Dispatcher ──────────────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> bool:
        parts = cmd.strip().split()
        command = parts[0].lower()

        if command in ("/exit", "/quit", "/q"):
            return False
        elif command == "/help":
            console.print(HELP)
        elif command == "/new":
            self.session_id = str(uuid.uuid4())
            console.print(f"[green]New session: {self.session_id[:8]}...[/green]")
        elif command == "/clear":
            await self._cmd_clear()
        elif command == "/history":
            await self._cmd_history()
        elif command == "/status":
            await self._cmd_status()
        elif command == "/config":
            self._cmd_config()
        elif command == "/schedule":
            await self._cmd_schedule()
        elif command == "/memory":
            query = " ".join(parts[1:]) if len(parts) > 1 else ""
            await self._cmd_memory(query)
        elif command == "/auth":
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "google":
                await self._cmd_auth_google()
            elif sub == "slack":
                console.print("\n[dim]Slack setup — add to .env:[/dim]")
                console.print("[cyan]SLACK_BOT_TOKEN=xoxb-...[/cyan]")
                console.print("[cyan]SLACK_SIGNING_SECRET=...[/cyan]")
                console.print(f"[dim]Event URL: {self.base_url}/webhooks/slack/events[/dim]")
            elif sub in ("wa", "whatsapp"):
                console.print("\n[dim]WhatsApp setup — add to .env:[/dim]")
                console.print("[cyan]TWILIO_ACCOUNT_SID=AC...[/cyan]")
                console.print("[cyan]TWILIO_AUTH_TOKEN=...[/cyan]")
                console.print("[cyan]TWILIO_WHATSAPP_NUMBER=+14155238886[/cyan]")
                console.print(f"[dim]Webhook URL: {self.base_url}/webhooks/whatsapp[/dim]")
            else:
                console.print("[dim]Usage: /auth google | slack | wa[/dim]")
        elif command == "/stream":
            sub = parts[1].lower() if len(parts) > 1 else "on"
            self.streaming = sub != "off"
            console.print(f"[dim]Streaming {'on' if self.streaming else 'off'}[/dim]")
        elif command == "/set-url":
            if len(parts) > 1:
                self.base_url = parts[1]
                self.config["api_url"] = parts[1]
                self._save_config()
                console.print(f"[green]URL → {parts[1]}[/green]")
        elif command == "/set-token":
            if len(parts) > 1:
                self.token = parts[1]
                self.config["token"] = parts[1]
                self._save_config()
                console.print("[green]Token saved[/green]")
        else:
            console.print(f"[red]Unknown: {command}[/red]  Try /help")
        return True

    # ── Main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._print_banner()

        if not self.token:
            console.print(Panel(
                "[yellow]Not authenticated.[/yellow]\n"
                "Run [cyan]/auth google[/cyan] to connect Gmail + Calendar\n"
                "Then [cyan]/auth slack[/cyan] and [cyan]/auth wa[/cyan] for more integrations",
                title="Setup",
                border_style="yellow",
            ))
            console.print()

        def _sigint(sig, frame):
            console.print("\n[dim](Ctrl+C — type /exit to quit)[/dim]")

        signal.signal(signal.SIGINT, _sigint)

        while True:
            try:
                user_input = input("\n[you] ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            self._save_history()

            if user_input.startswith("/"):
                if not await self._handle_command(user_input):
                    console.print("[dim]Bye.[/dim]")
                    break
            else:
                if self.streaming:
                    await self._stream(user_input)
                else:
                    await self._chat(user_input)


def main():
    cli = CLI()
    try:
        asyncio.run(cli.run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
