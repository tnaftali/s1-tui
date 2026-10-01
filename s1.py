"""s1 — type a state + question, see the typed System One decision and its confidence.

Backends: laya-mlx (local, loaded once) or jev/TypeSafe (hosted API) — switch via the
command palette. One question at a time.
Run: uv run python s1.py
"""

import json
import os
import time
import urllib.request

from rich.text import Text
from textual.app import App, ComposeResult
from textual.command import DiscoveryHit, Hit, Provider
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)

MODEL_ID = "aac6fef/laya-multilingual-mlx"  # same weights as the Core ML gate

# S1_DEMO=<key> preloads one of these fixed scenes (no model) for reproducible screenshots.
_DEMO = {
    "main": dict(
        state="Payment gateway returning 503 on every checkout; started 4 min ago, ~100% of attempts.",
        instructions="Is this an urgent incident?",
        qdef={"type": "noul", "instructions": "Is this an urgent incident?"},
        ans={"type": "noul", "noul": 0.94, "confidence": 0.88},
    ),
    "choice": dict(
        state='Customer email: "I was charged twice for order #4821 — please refund the extra one."',
        instructions="Route this ticket",
        qdef={"type": "choice", "instructions": "Route this ticket",
              "criteria": {"billing": "payments, refunds, charges",
                           "technical": "bugs, errors, outages",
                           "sales": "pricing, plans, upgrades"}},
        ans={"type": "choice", "choice": "billing",
             "probabilities": {"billing": 0.86, "technical": 0.09, "sales": 0.05},
             "confidence": 0.82},
    ),
    "score": dict(
        state="PR deletes the null-check guarding charge_card() before the amount is validated.",
        instructions="Rate the risk of merging",
        qdef={"type": "score", "instructions": "Rate the risk of merging",
              "criteria": ["safe", "low", "medium", "high", "critical"]},
        ans={"type": "score", "score": 3.1,
             "legend": {"0": "safe", "1": "low", "2": "medium", "3": "high", "4": "critical"},
             "probabilities": {"0": 0.02, "1": 0.08, "2": 0.19, "3": 0.44, "4": 0.27},
             "confidence": 0.61},
    ),
}
_DEMO["model-picker"] = _DEMO["main"]

# backends: local laya (loaded once) + hosted JEV/TypeSafe over HTTPS.
BACKENDS = {"laya": "laya  (local)", "jev": "jev  (typesafe api)"}
JEV_URL = "https://api.typesafe.ai/v1/systemone"
# every run is appended here as one JSON line (state, question, answer, probs, latency) for later review.
LOG_PATH = os.environ.get("S1_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "s1_runs.jsonl"))

# Catppuccin Macchiato palette
CAT = {
    "base": "#24273a", "mantle": "#1e2030", "crust": "#181926",
    "surface0": "#363a4f", "surface1": "#494d64", "surface2": "#5b6078",
    "text": "#cad3f5", "subtext0": "#a5adcb", "overlay0": "#6e738d",
    "blue": "#8aadf4", "mauve": "#c6a6f5", "peach": "#f5a97f",
    "green": "#a6da95", "yellow": "#eed49f", "red": "#ed8796",
}
DIM = CAT["subtext0"]     # labels / secondary text
FAINT = CAT["overlay0"]   # empty bar / hints
LABEL = CAT["text"]

# confidence colour bands
def band(conf: float) -> str:
    if conf >= 0.8:
        return CAT["green"]
    if conf >= 0.5:
        return CAT["peach"]
    return CAT["red"]


def bar(frac: float, colour: str, width: int = 20) -> Text:
    filled = round(max(0.0, min(1.0, frac)) * width)
    t = Text()
    t.append("█" * filled, style=colour)
    t.append("░" * (width - filled), style=FAINT)
    return t


class AppCommands(Provider):
    """Command-palette commands in a fixed order: keys, maximize, screenshot, theme, model, quit.

    The palette sorts discovery hits by their `text`, so each hit gets a numeric
    order key while displaying its real title.
    """

    def _items(self):
        app, screen = self.app, self.screen
        items = []
        if screen.query("HelpPanel"):
            items.append(("Keys", "Hide the keys and widget help panel", app.action_hide_help_panel))
        else:
            items.append(("Keys", "Show help for the focused widget and a summary of keys", app.action_show_help_panel))
        if screen.maximized is not None:
            items.append(("Minimize", "Restore the widget to normal size", screen.action_minimize))
        elif screen.focused is not None and screen.focused.allow_maximize:
            items.append(("Maximize", "Maximize the focused widget", screen.action_maximize))
        items.append(("Screenshot", "Save an SVG screenshot of the current screen",
                      lambda: app.set_timer(0.1, app.deliver_screenshot)))
        items.append(("Theme", "Change the theme (previews as you scroll)", app.action_pick_theme))
        items.append(("Model", "Switch backend: laya (local) or jev (TypeSafe API)", app.action_pick_model))
        items.append(("Quit", "Quit the application", app.action_quit))
        return items

    async def discover(self):
        for i, (title, help_, cb) in enumerate(self._items()):
            yield DiscoveryHit(title, cb, text=f"{i:02d}", help=help_)

    async def search(self, query: str):
        matcher = self.matcher(query)
        for title, help_, cb in self._items():
            score = matcher.match(title)
            if score > 0:
                yield Hit(score, matcher.highlight(title), cb, help=help_)


class LayaTUI(App):
    COMMANDS = {AppCommands}
    CSS = """
    #body { height: 1fr; }
    #left, #right { width: 1fr; padding: 1 2; }
    #left { border-right: solid $surface; }
    #state { height: 6; }
    #instructions { margin-top: 1; }
    #options { height: 9; margin-top: 1; }
    RadioSet { margin-top: 1; }
    #buttons { height: auto; margin-top: 1; }
    #buttons Button { margin-right: 2; }
    #result { height: 1fr; }
    #history { height: 20; border-top: solid $surface; padding-top: 1; }
    .dim { color: $text-muted; }
    """
    BINDINGS = [
        ("ctrl+r", "run", "Run"),
        ("ctrl+l", "clear", "Clear"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.agent = None
        self.backend = "laya"
        self.history: list[Text] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Label("state")
                yield TextArea(id="state")
                with RadioSet(id="type"):
                    yield RadioButton("noul  (yes/no)", value=True, id="t_noul")
                    yield RadioButton("choice  (pick one)", id="t_choice")
                    yield RadioButton("score  (ordered ladder)", id="t_score")
                yield Label("instructions", classes="dim")
                yield Input(placeholder="Is this an urgent incident?", id="instructions")
                yield Label("options — one per line, choice/score only", classes="dim")
                yield TextArea(id="options", disabled=True)
                with Horizontal(id="buttons"):
                    yield Button("Run  (ctrl+r)", id="run", variant="primary", disabled=True)
                    yield Button("Clear  (ctrl+l)", id="clear", variant="default")
            with Vertical(id="right"):
                yield Static("loading model…", id="result")
                yield Label("history — ↑↓ + enter (or click) to reload", classes="dim")
                yield ListView(id="history")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "catppuccin-macchiato"  # built-in, official palette
        self.title = "s1"  # the tool; subtitle shows the active backend · model
        self.query_one(Header).icon = "●"  # default ⭘ (U+2B58) is absent from JetBrains Mono → tofu
        self._set_subtitle()
        self.query_one("#state", TextArea).focus()
        if os.environ.get("S1_DEMO"):
            # demo = fixed seeded rows only; never read the real log, so screenshots can't leak it
            self._run_demo(os.environ["S1_DEMO"])
            return
        self._load_history()  # restore prior sessions' runs from the JSONL log
        self.load_model()

    # --- demo preload: fixed scenes for screenshots (S1_DEMO=main|choice|score|model-picker) ---
    def _run_demo(self, which: str) -> None:
        self.agent = object()  # fake "warm" so the subtitle reads laya · … · warm
        self.query_one("#run", Button).disabled = False
        self._set_subtitle()
        for ex, be in [(_DEMO["score"], "laya"), (_DEMO["choice"], "jev"), (_DEMO["main"], "laya")]:
            _, item, *_ = self._build_item(ex["ans"], 6.0, ex["state"], ex["qdef"], be)
            self._add_history(item)
        ex = _DEMO.get(which if which in _DEMO else "main")
        be = "jev" if which == "choice" else "laya"
        self.backend = be
        self._set_subtitle()
        self.query_one("#state", TextArea).text = ex["state"]
        self.query_one("#instructions", Input).value = ex["instructions"]
        crit = ex["qdef"].get("criteria")
        if crit:
            lines = [f"{k}: {v}" for k, v in crit.items()] if isinstance(crit, dict) else list(crit)
            self.query_one(f"#t_{ex['qdef']['type']}", RadioButton).value = True
            self.query_one("#options", TextArea).text = "\n".join(lines)
        out, *_ = self._build_item(ex["ans"], 5.8 if be == "laya" else 212.0,
                                   ex["state"], ex["qdef"], be)
        self.query_one("#result", Static).update(out)
        if which == "model-picker":
            self.push_screen(ModelPicker())

    def action_pick_theme(self) -> None:
        self.push_screen(ThemePicker())

    def action_pick_model(self) -> None:
        self.push_screen(ModelPicker())

    def set_backend(self, backend: str) -> None:
        self.backend = backend
        self._set_subtitle()
        if backend == "jev" and not self._jev_key():
            self.query_one("#result", Static).update(
                Text("jev selected — set TYPESAFE_API_KEY (or JEV_API_KEY) before Run", style=CAT["peach"])
            )

    def _set_subtitle(self) -> None:
        name = MODEL_ID.split("/")[-1] if self.backend == "laya" else "jev-latest"
        warm = " · warm" if (self.backend == "laya" and self.agent) else ""
        self.sub_title = f"{self.backend} · {name}{warm}"

    @staticmethod
    def _jev_key() -> str | None:
        return os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")

    # --- model load (once, in a thread) ---
    def load_model(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True)

    def _load(self) -> None:
        try:
            import laya_mlx as laya
        except ImportError:  # jev-only install (pip install 's1[local]' adds the local backend)
            self.call_from_thread(
                self.query_one("#result", Static).update,
                Text("laya (local) not installed — run `uv run --extra local s1`, "
                     "or switch to jev in the palette (ctrl+p → Model).", style=CAT["peach"]),
            )
            return
        agent = laya.load(MODEL_ID, dtype="float16")
        self.call_from_thread(self._loaded, agent)

    def _loaded(self, agent) -> None:
        self.agent = agent
        self.query_one("#run", Button).disabled = False
        self.query_one("#result", Static).update(
            Text("model warm — enter a state and Run", style=DIM)
        )
        self._set_subtitle()

    # --- type selection toggles the options field ---
    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        is_noul = event.pressed.id == "t_noul"
        self.query_one("#options", TextArea).disabled = is_noul

    def _selected_type(self) -> str:
        pressed = self.query_one("#type", RadioSet).pressed_button
        return {"t_noul": "noul", "t_choice": "choice", "t_score": "score"}[pressed.id]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.action_run()
        elif event.button.id == "clear":
            self.action_clear()

    def action_clear(self) -> None:
        self.query_one("#state", TextArea).text = ""
        self.query_one("#instructions", Input).value = ""
        self.query_one("#options", TextArea).text = ""
        self.query_one("#result", Static).update(
            Text("cleared — enter a state and Run", style=DIM)
        )
        self.query_one("#state", TextArea).focus()

    def action_run(self) -> None:
        if self.backend == "laya" and self.agent is None:
            return
        state = self.query_one("#state", TextArea).text.strip()
        qtype = self._selected_type()
        instructions = self.query_one("#instructions", Input).value.strip()
        if not state or not instructions:
            self.query_one("#result", Static).update(
                Text("need both a state and instructions", style=CAT["red"])
            )
            return
        qdef = {"type": qtype, "instructions": instructions}
        if qtype != "noul":
            lines = [o.strip() for o in self.query_one("#options", TextArea).text.splitlines() if o.strip()]
            if len(lines) < 2:
                self.query_one("#result", Static).update(
                    Text("choice/score need 2+ options (one per line)", style=CAT["red"])
                )
                return
            # choice: "label: description" per line → dict criteria {label: desc}; bare labels → list.
            # score: always an ordered array low→high (no descriptions); JEV rejects a map here.
            if qtype == "choice" and any(":" in ln for ln in lines):
                qdef["criteria"] = {
                    (kv := ln.split(":", 1))[0].strip(): (kv[1].strip() if len(kv) > 1 else "")
                    for ln in lines
                }
            else:
                qdef["criteria"] = lines
        self.query_one("#result", Static).update(Text("thinking…", style=DIM))
        self.query_one("#state", TextArea).focus()  # drop the Run button's stuck focus highlight
        self.run_worker(lambda: self._predict(state, qdef, instructions), thread=True, exclusive=True)

    def _predict(self, state: str, qdef: dict, instructions: str) -> None:
        backend = self.backend  # pin it — could change mid-run
        t0 = time.perf_counter()
        try:
            if backend == "jev":
                ans = self._jev_predict(state, qdef)
            else:
                ans = self.agent.predict(state, {"q": qdef})["answers"]["q"]
        except Exception as e:  # network / auth / model error → show it, don't crash the app
            self.call_from_thread(
                lambda: self.query_one("#result", Static).update(Text(f"error: {e}", style=CAT["red"]))
            )
            return
        ms = (time.perf_counter() - t0) * 1000
        self.call_from_thread(self._show, ans, ms, state, qdef, backend)

    def _jev_predict(self, state: str, qdef: dict) -> dict:
        """Call TypeSafe /v1/systemone, normalize the answer to laya-mlx's shape for _show."""
        key = self._jev_key()
        if not key:
            raise RuntimeError("no API key — set TYPESAFE_API_KEY or JEV_API_KEY")
        q = dict(qdef)
        # JEV choice REQUIRES criteria as a map {label: description}; bare labels (a list) → 422.
        # laya accepts the list, so we only reshape here at the JEV boundary. Score stays an array.
        if q.get("type") == "choice" and isinstance(q.get("criteria"), list):
            q["criteria"] = {label: label for label in q["criteria"]}
        body = json.dumps({"state": state, "model": "jev-latest", "questions": {"q": q}}).encode()
        req = urllib.request.Request(
            JEV_URL, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = json.loads(r.read())["answers"]["q"]
        t = qdef["type"]
        # wire shape verified against docs.typesafe.ai (quickstart + primitives/score), 2026-09-30.
        out = {"type": t}
        if t == "noul":
            out["noul"] = raw["noul"]
            out["confidence"] = raw.get("confidence", raw["noul"])  # noul: the prob IS the confidence
        elif t == "choice":
            out.update(choice=raw["choice"], probabilities=raw["probabilities"], confidence=raw["confidence"])
        else:
            out.update(score=raw["score"], legend=raw["legend"],
                       probabilities=raw["probabilities"], confidence=raw["confidence"])
        return out

    # --- render ---
    def _show(self, ans: dict, ms: float, state: str, qdef: dict, backend: str = "laya") -> None:
        out, item, selected, conf, probs = self._build_item(ans, ms, state, qdef, backend)
        self.query_one("#result", Static).update(out)
        self._add_history(item)
        model = MODEL_ID.split("/")[-1] if backend == "laya" else "jev-latest"
        self._log_run(backend, model, state, qdef, ans, ms, selected, conf, probs)

    def _add_history(self, item: ListItem) -> None:
        hist = self.query_one("#history", ListView)
        hist.insert(0, [item])
        for stale in list(hist.children)[15:]:  # keep last 15
            stale.remove()

    def _build_item(self, ans: dict, ms: float, state: str, qdef: dict, backend: str):
        """Render the result panel + a history ListItem from an answer. Shared by live runs and reload."""
        instructions = qdef["instructions"]
        qtype = ans["type"]
        if qtype == "noul":
            p_true = ans["noul"]
            selected = "true" if p_true >= 0.5 else "false"
            probs = {"true": p_true, "false": round(1 - p_true, 4)}
        elif qtype == "choice":
            selected = ans["choice"]
            probs = ans["probabilities"]
        else:  # score
            legend = ans["legend"]
            probs = {legend[i]: v for i, v in ans["probabilities"].items()}
            selected = max(probs, key=probs.get)
        # headline = mass on the pick (consistent across types; for noul == laya's confidence).
        # laya's own `confidence` field is a calibrated margin signal, shown separately below.
        conf = probs[selected]
        calibrated = ans["confidence"]
        colour = band(conf)

        out = Text()
        out.append("  ▸ ", style=DIM)
        out.append(f"{selected}\n\n", style=f"bold {colour}")
        out.append("  confidence  ", style=DIM)
        out.append(f"{conf:.3f}\n  ", style=f"bold {colour}")
        out.append_text(bar(conf, colour))
        out.append(f" {conf*100:.0f}%\n", style=colour)
        out.append(f"  calibrated  {calibrated:.3f}\n\n", style=DIM)
        if qtype == "score":
            out.append(f"  expected level  {ans['score']:.2f}\n\n", style=DIM)
        out.append("  probabilities\n", style=DIM)
        for label, p in sorted(probs.items(), key=lambda kv: kv[1], reverse=True):
            out.append(f"   {label[:16]:<17}", style=LABEL)
            out.append_text(bar(p, band(p), width=12))
            out.append(f" {p:.3f}\n")
        model = MODEL_ID.split("/")[-1] if backend == "laya" else "jev-latest"
        btag = CAT["mauve"] if backend == "laya" else CAT["blue"]
        out.append(f"\n  latency  {ms:.1f} ms\n", style=DIM)
        out.append("  via  ", style=DIM)
        out.append(f"{backend} · {model}", style=btag)

        row = Text()
        row.append(f"{conf:.3f} ", style=f"bold {colour}")
        row.append(f"{backend:<4} ", style=btag)
        row.append(f"{selected:<9} ", style=LABEL)
        row.append(instructions[:34], style=DIM)
        item = ListItem(Static(row))
        item.query_data = {  # full query, to reload on select
            "state": state,
            "type": qtype,
            "instructions": instructions,
            "options": qdef.get("criteria", []),
        }
        item.result = out  # the rendered answer panel, shown again on click
        return out, item, selected, conf, probs

    def _load_history(self) -> None:
        """Rebuild the last 15 runs from the JSONL log so history survives restarts."""
        try:
            with open(LOG_PATH) as f:
                recs = [json.loads(ln) for ln in f if ln.strip()][-15:]
        except FileNotFoundError:
            return
        except Exception:
            return
        for rec in recs:  # oldest→newest; _add_history inserts at top, so newest ends on top
            qdef = {"type": rec["type"], "instructions": rec["instructions"]}
            if rec.get("criteria") is not None:
                qdef["criteria"] = rec["criteria"]
            _, item, *_ = self._build_item(
                rec["answer"], rec.get("latency_ms", 0.0), rec["state"], qdef, rec.get("backend", "laya")
            )
            self._add_history(item)

    def _log_run(self, backend, model, state, qdef, ans, ms, selected, conf, probs) -> None:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "backend": backend, "model": model,
            "state": state, "type": qdef["type"], "instructions": qdef["instructions"],
            "criteria": qdef.get("criteria"),
            "selected": selected, "confidence": round(conf, 4), "probabilities": probs,
            "answer": ans, "latency_ms": round(ms, 1),
        }
        try:
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:  # logging must never break a run
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        data = getattr(event.item, "query_data", None)
        if not data:
            return
        self.query_one(f"#t_{data['type']}", RadioButton).value = True  # toggles options field
        self.query_one("#state", TextArea).text = data["state"]
        self.query_one("#instructions", Input).value = data["instructions"]
        opts = data["options"]
        # criteria is a list (bare labels / score ladder) or a dict (choice label: desc) — render both back.
        lines = [f"{k}: {v}" for k, v in opts.items()] if isinstance(opts, dict) else list(opts)
        self.query_one("#options", TextArea).text = "\n".join(lines)
        result = getattr(event.item, "result", None)
        if result is not None:  # show the answer we actually got last run, no re-run
            self.query_one("#result", Static).update(result)
        self.query_one("#state", TextArea).focus()


class ThemePicker(ModalScreen):
    """Theme list that previews live while you scroll; enter keeps, esc reverts."""

    CSS = """
    ThemePicker { align: center middle; }
    #picker { width: 50; height: auto; max-height: 95%; border: round $border; background: $surface; padding: 0 1; }
    #picker-title { padding: 1 0 0 1; color: $text-muted; }
    #themes { height: auto; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label("theme — ↑↓ preview · enter keep · esc revert", id="picker-title")
            yield OptionList(*sorted(self.app.available_themes), id="themes")

    def on_mount(self) -> None:
        self._original = self.app.theme
        names = sorted(self.app.available_themes)
        ol = self.query_one(OptionList)
        if self._original in names:
            ol.highlighted = names.index(self._original)
        ol.focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.app.theme = event.option.prompt  # live preview

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.app.theme = event.option.prompt
        self.dismiss()

    def action_cancel(self) -> None:
        self.app.theme = self._original
        self.dismiss()


class ModelPicker(ModalScreen):
    """Pick the backend: laya (local) or jev (TypeSafe API). Enter selects, esc cancels."""

    CSS = """
    ModelPicker { align: center middle; }
    #picker { width: 50; height: auto; border: round $border; background: $surface; padding: 0 1; }
    #picker-title { padding: 1 0 0 1; color: $text-muted; }
    #models { height: auto; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label("model — ↑↓ · enter select · esc cancel", id="picker-title")
            yield OptionList(*BACKENDS.values(), id="models")

    def on_mount(self) -> None:
        keys = list(BACKENDS)
        ol = self.query_one(OptionList)
        ol.highlighted = keys.index(self.app.backend)
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.app.set_backend(list(BACKENDS)[event.option_index])
        self.dismiss()

    def action_cancel(self) -> None:
        self.dismiss()


def main() -> None:
    LayaTUI().run()


if __name__ == "__main__":
    main()
