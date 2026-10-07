"""Double Feature: compare two Letterboxd accounts and pick a film together."""

import html
import io
import random
import string
import threading
import time
import zipfile
import zlib

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Double Feature", page_icon="🎟️", layout="centered",
                   initial_sidebar_state="collapsed")

GOLD = "#F2C14E"
SEA = "#8EC5C0"
CREAM = "#F3ECDD"
MUTED = "#98A2B5"
EMPTY = pd.DataFrame(columns=["Date", "Name", "Year", "Letterboxd URI"])


# ---------- Loading a Letterboxd export ----------

def _find(z: zipfile.ZipFile, filename: str):
    """Find a CSV in the export, skipping the deleted/orphaned/likes/lists folders."""
    skip = {"deleted", "orphaned", "likes", "lists"}
    for n in z.namelist():
        parts = n.split("/")
        if parts[-1] == filename and not skip.intersection(parts[:-1]):
            return n
    return None


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("Name", "Year", "Letterboxd URI"):
        if col not in df.columns:
            df[col] = pd.NA
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce").astype("Int64")
    # Match films across accounts on title + year
    df["key"] = df["Name"].astype(str).str.strip().str.casefold() + "|" + df["Year"].astype(str)
    return df.drop_duplicates("key", keep="last")


@st.cache_data(show_spinner=False)
def load_export(raw: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(raw))
    out = {"found": 0}
    for name in ("watched", "ratings", "watchlist"):
        path = _find(z, f"{name}.csv")
        out["found"] += bool(path)
        out[name] = _clean(pd.read_csv(z.open(path)) if path else EMPTY)
    if "Rating" not in out["ratings"].columns:
        out["ratings"]["Rating"] = pd.Series(dtype=float)
    # profile.csv gives a default display name: given name, else Letterboxd username
    out["name"] = ""
    path = _find(z, "profile.csv")
    if path:
        try:
            prof = pd.read_csv(z.open(path), dtype=str).fillna("")
            if len(prof):
                row = prof.iloc[0]
                out["name"] = (row.get("Given Name", "") or row.get("Username", "")).strip()
        except (pd.errors.ParserError, UnicodeDecodeError):
            pass
    return out


def summarise(d: dict) -> dict:
    seen = set(d["watched"]["key"]) | set(d["ratings"]["key"])
    ratings = pd.to_numeric(d["ratings"].set_index("key")["Rating"], errors="coerce").dropna()
    return {
        "seen": seen,
        "watchlist": set(d["watchlist"]["key"]) - seen,
        "ratings": ratings,
    }


def build_catalog(*exports: dict) -> pd.DataFrame:
    frames = [
        d[k][["key", "Name", "Year", "Letterboxd URI"]]
        for d in exports
        for k in ("watchlist", "watched", "ratings")
        if len(d[k])
    ]
    if not frames:
        return EMPTY.assign(key=[]).set_index("key")
    return pd.concat(frames).drop_duplicates("key").set_index("key")


# ---------- Styling ----------

GRAIN = ("url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'>"
         "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/>"
         "<feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .55 0'/></filter>"
         "<rect width='100%' height='100%' filter='url(%23n)' opacity='.16'/></svg>\")")

CSS = """
<style>
:root {
  --gold: #F2C14E; --gold-deep: #D99A2B; --sea: #8EC5C0; --coral: #EE8A6D;
  --ink: #111926; --surface: #1B2536; --surface-2: #222E42; --line: rgba(243,236,221,.10);
  --cream: #F3ECDD; --muted: #98A2B5;
  --display: "Fraunces", Georgia, serif; --sans: "DM Sans", system-ui, sans-serif;
  --label: "Bebas Neue", "DM Sans", sans-serif;
  --stars: "DejaVu Sans", "Segoe UI Symbol", "Apple Symbols", sans-serif;
}
@property --p { syntax: "<number>"; inherits: true; initial-value: 0; }

/* Page: deep navy, warm projector glow from the top, a little film grain */
.stApp {
  background:
    GRAIN_URL,
    radial-gradient(110% 55% at 50% -12%, rgba(242,193,78,.20), rgba(242,193,78,0) 62%),
    radial-gradient(70% 40% at 100% 105%, rgba(142,197,192,.10), rgba(142,197,192,0) 70%),
    #111926;
  background-attachment: fixed;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"], .block-container { max-width: 560px; padding: 1rem 1rem 6rem; }
[data-testid="stMarkdownContainer"] p { margin-bottom: 0; }
a { -webkit-tap-highlight-color: transparent; }

/* ---------- Marquee header ---------- */
.marquee {
  position: relative; border-radius: 22px; padding: 11px; margin: .5rem 0 1.1rem;
  background:
    radial-gradient(circle, #FFF1C4 0 2.2px, rgba(242,193,78,.55) 3px, rgba(242,193,78,0) 6px) 0 0 / 15px 15px,
    linear-gradient(180deg, #3A2A12, #241A0C);
  box-shadow: 0 0 0 1px rgba(242,193,78,.35), 0 18px 50px -18px rgba(242,193,78,.45);
  animation: chase 1.6s steps(2) infinite;
}
@keyframes chase { to { background-position: 7.5px 0, 0 0; } }
.marquee-in {
  border-radius: 13px; padding: 1.15rem 1rem 1.05rem; text-align: center;
  background: linear-gradient(180deg, #1A2232, #121A27);
  box-shadow: inset 0 0 0 1px rgba(242,193,78,.28), inset 0 10px 30px rgba(0,0,0,.35);
}
.mq-kick { font-family: var(--label); letter-spacing: .32em; font-size: .86rem; color: var(--gold);
  display: flex; align-items: center; justify-content: center; gap: .6rem; }
.mq-kick:before, .mq-kick:after { content: ""; height: 1px; width: 2.2rem;
  background: linear-gradient(90deg, rgba(242,193,78,0), rgba(242,193,78,.8)); }
.mq-kick:after { transform: scaleX(-1); }
.mq-title { font-family: var(--display); font-weight: 600; font-size: 2.55rem; line-height: 1.02;
  letter-spacing: -.01em; margin: .35rem 0 .45rem; color: var(--cream);
  text-shadow: 0 0 24px rgba(242,193,78,.25); font-variation-settings: "SOFT" 60, "WONK" 1; }
.mq-title em { font-style: italic; color: var(--gold); font-weight: 400; padding: 0 .05em; }
.mq-sub { font-family: var(--label); letter-spacing: .1em; font-size: .95rem; white-space: nowrap; color: var(--muted); }
.mq-sub b { color: var(--cream); font-weight: 400; }
.mq-sub i { font-style: normal; color: rgba(242,193,78,.6); padding: 0 .4rem; }

/* ---------- Tabs as a pill bar ---------- */
[data-testid="stTabs"] [role="tablist"] {
  gap: 4px; padding: 5px; border-radius: 999px; background: rgba(27,37,54,.85);
  box-shadow: inset 0 0 0 1px var(--line); margin-bottom: .6rem; overflow: visible;
}
[data-testid="stTabs"] [role="tab"] {
  flex: 1 1 0; justify-content: center; border-radius: 999px; padding: .55rem 0 .42rem; margin: 0;
  height: auto; border: 0 !important; background: transparent; transition: background .2s, color .2s;
}
[data-testid="stTabs"] [role="tab"] p { font-family: var(--label); font-size: 1.12rem !important;
  letter-spacing: .12em; color: var(--muted); }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
  background: linear-gradient(180deg, #F6CF6B, var(--gold)); box-shadow: 0 6px 18px -6px rgba(242,193,78,.6);
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { color: var(--ink); }
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"], [data-testid="stTabs"] .react-aria-SelectionIndicator { display: none !important; }

/* ---------- Widgets ---------- */
[data-testid="stExpander"] details { border-radius: 16px; border: 1px solid var(--line);
  background: rgba(27,37,54,.7); }
[data-testid="stExpander"] summary p { font-weight: 600; }
[data-testid="stBaseButton-primary"] {
  min-height: 3.5rem; border: 0;
  background: linear-gradient(180deg, #F7D06E 0%, var(--gold) 55%, var(--gold-deep) 100%);
  box-shadow: 0 10px 28px -10px rgba(242,193,78,.7), inset 0 1px 0 rgba(255,255,255,.45);
}
[data-testid="stBaseButton-primary"] p { font-family: var(--label); font-size: 1.45rem !important;
  letter-spacing: .14em; color: var(--ink); padding-top: .15rem; }
[data-testid="stBaseButton-primary"]:hover { filter: brightness(1.05); }
[data-testid="stBaseButton-primary"]:disabled { opacity: .4; box-shadow: none; filter: saturate(.6); }
[data-testid="stBaseButton-primary"]:active { transform: translateY(1px) scale(.995); }
button[data-variant="pills"], button[data-variant="segmented_control"] { min-height: 2.4rem; }
button[data-variant="pills"][aria-pressed="true"], button[data-variant="segmented_control"][aria-checked="true"] {
  background: rgba(242,193,78,.16) !important; border-color: rgba(242,193,78,.7) !important; color: var(--gold) !important; }
[data-testid="stTabPanel"] > div > div:first-child .sec, [data-testid="stTabPanel"] .sec.first { margin-top: .6rem; }
[data-testid="stFileUploaderDropzone"] { border: 1.5px dashed rgba(242,193,78,.35); background: rgba(17,25,38,.6); }
[data-testid="stCaptionContainer"] { color: var(--muted); }

/* ---------- Section headings ---------- */
.sec { margin: 1.9rem 0 .8rem; }
.sec-k { font-family: var(--label); letter-spacing: .22em; font-size: .9rem; color: var(--gold); }
.sec-t { font-family: var(--display); font-weight: 600; font-size: 1.6rem; line-height: 1.1; margin: .1rem 0 0;
  font-variation-settings: "SOFT" 50; }
.sec-n { color: var(--muted); font-size: .9rem; margin-top: .35rem; line-height: 1.45; }
.note { border-radius: 16px; padding: 1rem 1.1rem; background: rgba(27,37,54,.75);
  box-shadow: inset 0 0 0 1px var(--line); color: var(--muted); line-height: 1.5; font-size: .95rem; }
.note b { color: var(--cream); font-weight: 600; }

/* ---------- Posters ---------- */
.poster { position: relative; display: flex; flex-direction: column; aspect-ratio: 2 / 3; overflow: hidden;
  border-radius: 6px; color: var(--pi); text-decoration: none !important; isolation: isolate;
  box-shadow: 0 1px 0 rgba(255,255,255,.12) inset, 0 10px 22px -12px rgba(0,0,0,.9), 0 0 0 1px rgba(0,0,0,.25); }
.poster, .poster:link, .poster:visited, .poster:hover { color: var(--pi) !important; }
.poster:after { content: ""; position: absolute; inset: 0; z-index: -1; background: GRAIN_URL; opacity: .6;
  mix-blend-mode: overlay; }
.poster:before { content: ""; position: absolute; inset: 0; z-index: 2; pointer-events: none;
  background: linear-gradient(115deg, rgba(255,255,255,.16), rgba(255,255,255,0) 38%); }
.p-t { font-family: var(--display); font-weight: 650; line-height: 1.04; padding: 9% 9% 0;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
  font-variation-settings: "SOFT" 100, "WONK" 1; letter-spacing: -.005em; }
.p-y { font-family: var(--label); letter-spacing: .14em; margin-top: auto; padding: 0 9% 7%; opacity: .85; }
.p-sm { width: 44px; flex: 0 0 44px; border-radius: 4px; }
.p-sm .p-t, .p-sm .p-y { display: none; }
.p-md .p-t { font-size: .82rem; } .p-md .p-y { font-size: .72rem; }
.p-lg { width: 92px; flex: 0 0 92px; } .p-lg .p-t { font-size: .8rem; } .p-lg .p-y { font-size: .7rem; }

.wall { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px 10px; }
.wall-item { min-width: 0; }
.wall-cap { font-size: .74rem; color: var(--muted); margin-top: .4rem; line-height: 1.3; }
.reel { display: flex; gap: 12px; overflow-x: auto; scroll-snap-type: x mandatory; padding: 2px 2px 10px;
  margin: 0 -1rem; padding-left: 1rem; scrollbar-width: none; }
.reel::-webkit-scrollbar { display: none; }
.reel-item { flex: 0 0 116px; scroll-snap-align: start; }
.reel-item:last-child { margin-right: 1rem; }
.reel-cap { margin-top: .5rem; font-size: .78rem; line-height: 1.5; }

/* ---------- Film rows ---------- */
.row { display: flex; align-items: center; gap: .85rem; padding: .7rem 0; border-bottom: 1px solid var(--line); }
.row:last-child { border-bottom: 0; }
.row-body { flex: 1; min-width: 0; }
.row-t { font-family: var(--display); font-size: 1.08rem; line-height: 1.22; font-weight: 500; }
.row-t a { color: var(--cream) !important; text-decoration: none; }
.row-y { font-family: var(--label); letter-spacing: .1em; color: var(--muted); font-size: .92rem; margin-left: .35rem; }
.row-s { font-size: .82rem; color: var(--muted); margin-top: .25rem; line-height: 1.55; }
.row-m { text-align: right; white-space: nowrap; }
.chip { display: inline-block; font-family: var(--label); letter-spacing: .12em; font-size: .78rem;
  padding: .2rem .55rem .1rem; border-radius: 999px; background: rgba(142,197,192,.14); color: var(--sea); }
.chip-gold { background: rgba(242,193,78,.14); color: var(--gold); }

/* Stars: five glyphs, filled to the rating, so half stars read like Letterboxd */
.st { font-family: var(--stars); letter-spacing: .04em; white-space: nowrap;
  background: linear-gradient(90deg, var(--c, var(--gold)) calc(var(--r) * 20%), rgba(243,236,221,.16) 0);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.dot { display: inline-block; width: .5rem; height: .5rem; border-radius: 50%; margin-right: .35rem;
  vertical-align: .06rem; }
.rl { display: flex; align-items: center; gap: .4rem; }
.rl .nm { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--cream); }

/* ---------- Ticket ---------- */
.ticket {
  --notch: 66px; display: flex; color: var(--ink); margin: .2rem 0 1rem; border-radius: 16px;
  background:
    GRAIN_URL,
    radial-gradient(120% 90% at 85% 0%, #FFE39A, rgba(255,227,154,0) 60%),
    linear-gradient(160deg, #F7D06E, var(--gold) 50%, #E6A936);
  -webkit-mask: radial-gradient(circle 11px at var(--notch) 0, #0000 98%, #000) top / 100% 51% no-repeat,
                radial-gradient(circle 11px at var(--notch) 100%, #0000 98%, #000) bottom / 100% 51% no-repeat;
          mask: radial-gradient(circle 11px at var(--notch) 0, #0000 98%, #000) top / 100% 51% no-repeat,
                radial-gradient(circle 11px at var(--notch) 100%, #0000 98%, #000) bottom / 100% 51% no-repeat;
  filter: drop-shadow(0 16px 30px rgba(242,193,78,.25));
}
.ticket.a { animation: deal-a .55s cubic-bezier(.2,.9,.25,1.15) both; }
.ticket.b { animation: deal-b .55s cubic-bezier(.2,.9,.25,1.15) both; }
@keyframes deal-a { from { opacity: 0; transform: translateY(-18px) rotate(-2.5deg) scale(.96); } }
@keyframes deal-b { from { opacity: 0; transform: translateY(-18px) rotate(2.5deg) scale(.96); } }
.tk-stub { flex: 0 0 var(--notch); display: flex; flex-direction: column; align-items: center; justify-content: center;
  border-right: 2px dashed rgba(17,25,38,.35); padding: 1rem 0; }
.tk-stub span { writing-mode: vertical-rl; transform: rotate(180deg); font-family: var(--label);
  letter-spacing: .3em; font-size: 1.35rem; line-height: 1; }
.tk-stub small { writing-mode: vertical-rl; transform: rotate(180deg); font-family: var(--label);
  letter-spacing: .2em; font-size: .72rem; opacity: .6; margin-top: .8rem; }
.tk-main { flex: 1; min-width: 0; padding: 1rem 1rem 1rem .95rem; }
.tk-kick { font-family: var(--label); letter-spacing: .26em; font-size: .85rem; opacity: .7; }
.tk-body { display: flex; gap: .9rem; margin-top: .55rem; align-items: flex-start; }
.tk-title { font-family: var(--display); font-weight: 700; font-size: 1.55rem; line-height: 1.03;
  letter-spacing: -.01em; overflow-wrap: anywhere; font-variation-settings: "SOFT" 80, "WONK" 1; }
.tk-year { font-family: var(--label); letter-spacing: .16em; font-size: 1.05rem; margin-top: .35rem; opacity: .75; }
.tk-why { font-size: .82rem; margin-top: .6rem; line-height: 1.35; opacity: .85; }
.tk-why .st { --c: var(--ink); background: linear-gradient(90deg, var(--ink) calc(var(--r) * 20%), rgba(17,25,38,.2) 0);
  -webkit-background-clip: text; background-clip: text; }
.tk-link { display: inline-flex; margin-top: .9rem; font-family: var(--label); letter-spacing: .14em; font-size: 1rem;
  padding: .45rem .9rem .32rem; border-radius: 999px; background: var(--ink); color: var(--gold) !important;
  text-decoration: none !important; }
.tk-ghost { border-radius: 16px; padding: 1.4rem 1.2rem; text-align: center; margin: .2rem 0 1rem;
  border: 1.5px dashed rgba(242,193,78,.35); color: var(--muted); }
.tk-ghost b { display: block; font-family: var(--display); font-size: 1.3rem; color: var(--cream); font-weight: 500;
  margin-bottom: .25rem; font-style: italic; }

/* ---------- Taste ---------- */
.match { display: flex; flex-direction: column; align-items: center; text-align: center; margin: .6rem 0 .4rem; }
.ring { display: grid; place-items: center; width: 196px; aspect-ratio: 1; position: relative;
  animation: fill 1.4s cubic-bezier(.2,.8,.2,1) both; }
@keyframes fill { from { --p: 0; } }
.ring:before { content: ""; position: absolute; inset: 0; border-radius: 50%;
  background: conic-gradient(var(--gold) 0, var(--sea) calc(var(--p) * 1%), rgba(243,236,221,.08) 0);
  -webkit-mask: radial-gradient(farthest-side, #0000 calc(100% - 15px), #000 calc(100% - 14px));
          mask: radial-gradient(farthest-side, #0000 calc(100% - 15px), #000 calc(100% - 14px));
  filter: drop-shadow(0 0 14px rgba(242,193,78,.25)); }
.ring b { font-family: var(--display); font-size: 3.4rem; font-weight: 600; line-height: 1; letter-spacing: -.02em; }
.ring b small { font-size: 1.6rem; color: var(--gold); margin-left: .05em; }
.ring span { display: block; font-family: var(--label); letter-spacing: .24em; font-size: .85rem; color: var(--muted);
  margin-top: .3rem; }
.verdict { font-family: var(--display); font-style: italic; font-size: 1.45rem; margin-top: 1rem; color: var(--cream); }
.verdict-n { color: var(--muted); font-size: .88rem; margin-top: .3rem; }
.trio { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 1.2rem 0 .2rem; }
.trio div { border-radius: 14px; background: rgba(27,37,54,.8); box-shadow: inset 0 0 0 1px var(--line);
  padding: .8rem .5rem .7rem; text-align: center; }
.trio b { display: block; font-family: var(--display); font-size: 1.45rem; font-weight: 600; line-height: 1.1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trio span { font-family: var(--label); letter-spacing: .12em; font-size: .78rem; color: var(--muted); }
.vs-gap { font-family: var(--display); font-size: 1.35rem; font-weight: 600; color: var(--coral); line-height: 1; }
.vs-gap small { display: block; font-family: var(--label); letter-spacing: .12em; font-size: .7rem;
  color: var(--muted); margin-top: .2rem; font-weight: 400; }

/* ---------- Stats: tale of the tape ---------- */
.tape { border-radius: 18px; background: rgba(27,37,54,.75); box-shadow: inset 0 0 0 1px var(--line); padding: .4rem 1rem .6rem; }
.tape-h { display: flex; justify-content: space-between; align-items: center; padding: .7rem 0 .6rem;
  border-bottom: 1px solid var(--line); font-family: var(--display); font-size: 1.15rem; font-weight: 600; }
.tape-h i { font-style: italic; font-weight: 400; color: var(--muted); font-size: .95rem; }
.tape-r { display: grid; grid-template-columns: 1fr auto 1fr; align-items: baseline; padding: .7rem 0 .25rem; }
.tape-r b { font-family: var(--display); font-weight: 600; font-size: 1.3rem; }
.tape-r b:last-of-type { text-align: right; }
.tape-r span { font-family: var(--label); letter-spacing: .14em; font-size: .82rem; color: var(--muted); text-align: center; }
.tape-bar { grid-column: 1 / -1; display: flex; height: 4px; border-radius: 4px; overflow: hidden; margin-top: .45rem;
  background: rgba(243,236,221,.08); gap: 2px; }
.tape-bar i { display: block; height: 100%; }
.together { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
.together div { border-radius: 18px; padding: 1rem; text-align: center;
  background: linear-gradient(160deg, rgba(242,193,78,.16), rgba(142,197,192,.12)); box-shadow: inset 0 0 0 1px rgba(242,193,78,.2); }
.together b { display: block; font-family: var(--display); font-size: 2.2rem; font-weight: 600; line-height: 1; }
.together span { font-family: var(--label); letter-spacing: .14em; font-size: .82rem; color: var(--muted); }

/* Butterfly charts: one person to the left, the other to the right */
.fly { margin-top: .2rem; }
.fly-h { display: grid; grid-template-columns: 1fr 4.6rem 1fr; font-family: var(--label); letter-spacing: .14em;
  font-size: .85rem; padding-bottom: .4rem; }
.fly-h span:first-child { text-align: right; color: var(--gold); } .fly-h span:last-child { color: var(--sea); }
.fly-r { display: grid; grid-template-columns: 1fr 4.6rem 1fr; align-items: center; height: 1.6rem; }
.fly-l { text-align: center; font-family: var(--label); letter-spacing: .1em; font-size: .9rem; color: var(--muted); }
.fly-l .st { font-size: .62rem; letter-spacing: 0; }
.fly-a, .fly-b { display: flex; align-items: center; gap: .35rem; font-size: .72rem; color: var(--muted); }
.fly-a { flex-direction: row-reverse; }
.fly-a i, .fly-b i { display: block; height: .95rem; min-width: 2px; }
.fly-a i[style^="width:0.0%"], .fly-b i[style^="width:0.0%"] { display: none; }
.fly-a i { background: linear-gradient(270deg, var(--gold), #E6A936); border-radius: 4px 2px 2px 4px; }
.fly-b i { background: linear-gradient(90deg, var(--sea), #6FAFAA); border-radius: 2px 4px 4px 2px; }

@media (prefers-reduced-motion: reduce) {
  .marquee, .ticket, .ring { animation: none !important; }
}
</style>
""".replace("GRAIN_URL", GRAIN)

st.markdown(CSS, unsafe_allow_html=True)

COLOURS = {}
esc = html.escape

# Poster palettes: (top, bottom, motif, ink)
PALETTES = [
    ("#2B1B3D", "#4A2A5E", "#F2C14E", "#F3ECDD"),
    ("#0F3B3A", "#1C5A57", "#9ED3CD", "#F3ECDD"),
    ("#7A2E25", "#B5452F", "#F4D6A0", "#FFF4E0"),
    ("#1E2F4F", "#2F4A7A", "#F28C6B", "#F3ECDD"),
    ("#EADBB8", "#D9C394", "#B5452F", "#2A1E18"),
    ("#141414", "#2A2525", "#E8463A", "#F3ECDD"),
    ("#3B5D3A", "#557A4E", "#F2E3B3", "#F8F1DC"),
    ("#F2C14E", "#E3A23A", "#1E2F4F", "#172131"),
    ("#5B2A44", "#8C3B5E", "#F7B2A8", "#FFEDE8"),
    ("#20384A", "#2E5470", "#F2E8CF", "#F2E8CF"),
    ("#C9D9D3", "#A8C3BA", "#1F4D4A", "#14302E"),
    ("#3A1F14", "#6B3A22", "#F2A65A", "#FBE7CF"),
]
MOTIFS = [
    "radial-gradient(circle at 50% 64%, {c} 0 21%, #0000 21.6%)",
    "linear-gradient(180deg, #0000 70%, #0003 70%), radial-gradient(circle at 50% 70%, {c} 0 24%, #0000 24.6%)",
    "linear-gradient(180deg, #0000 54%, {c} 54% 60%, #0000 60% 66%, {c} 66% 72%, #0000 72% 78%, {c} 78% 84%, #0000 84%)",
    "linear-gradient(140deg, #0000 56%, {c}D9 56%)",
    "radial-gradient(circle at 50% 66%, {c} 0 4%, #0000 4% 9%, {c} 9% 12%, #0000 12% 17%, {c} 17% 20%, #0000 20% 25%, {c} 25% 28%, #0000 28%)",
    "radial-gradient(ellipse 46% 36% at 50% 100%, {c} 0 99%, #0000 100%)",
    "conic-gradient(from 155deg at 50% 0%, #0000 0deg, {c}66 22deg 50deg, #0000 50deg)",
    "radial-gradient(circle at 37% 64%, {c}D0 0 19%, #0000 19.6%), radial-gradient(circle at 63% 64%, {c}80 0 19%, #0000 19.6%)",
    "linear-gradient(90deg, #0000 30%, {c} 30% 36%, #0000 36% 64%, {c} 64% 70%, #0000 70%) 0 72% / 100% 46% no-repeat",
]


def poster(k, size="md", link=True) -> str:
    r = cat.loc[k]
    return poster_html(k, r["Name"], year_str(r["Year"]), r["Letterboxd URI"], size, link)


def poster_html(k, name, year, uri, size="md", link=True) -> str:
    """A tiny screen-printed 'poster' generated from the film's title, so every film has art."""
    h = zlib.crc32(str(k).encode())
    i = h % len(PALETTES)
    if size == "lg" and PALETTES[i][0] == GOLD:  # don't put a gold poster on the gold ticket
        i = (i + 1) % len(PALETTES)
    top, bottom, c, ink = PALETTES[i]
    motif = MOTIFS[(h // 13) % len(MOTIFS)].format(c=c)
    style = f"--pi:{ink};background:{motif},linear-gradient(170deg,{top},{bottom})"
    inner = f'<span class="p-t">{esc(str(name))}</span><span class="p-y">{esc(str(year or ""))}</span>'
    if link and pd.notna(uri) and uri:
        return f'<a class="poster p-{size}" style="{style}" href="{esc(str(uri))}" target="_blank">{inner}</a>'
    return f'<div class="poster p-{size}" style="{style}">{inner}</div>'


def stars(r) -> str:
    if pd.isna(r):
        return ""
    return "★" * int(r) + ("½" if r % 1 else "")


def star_bar(r, colour=None) -> str:
    c = f"--c:{colour};" if colour else ""
    return f'<span class="st" style="{c}--r:{float(r)}" title="{float(r):g} stars">★★★★★</span>'


def dot(name: str) -> str:
    return f'<span class="dot" style="background:{COLOURS.get(name, GOLD)}"></span>'


def rating_line(name: str, r) -> str:
    return (f'<div class="rl">{dot(name)}<span class="nm">{esc(name)}</span>'
            f'{star_bar(r, COLOURS.get(name))}</div>')


def year_str(y) -> str:
    return str(int(y)) if pd.notna(y) else ""


def title_link(k) -> str:
    r = cat.loc[k]
    name = esc(str(r["Name"]))
    uri = r["Letterboxd URI"]
    return f'<a href="{esc(str(uri))}" target="_blank">{name}</a>' if pd.notna(uri) else name


def md(s: str):
    st.markdown(s, unsafe_allow_html=True)


def section(title: str, kicker: str = "", note: str = "", first: bool = False):
    k = f'<div class="sec-k">{kicker}</div>' if kicker else ""
    n = f'<div class="sec-n">{note}</div>' if note else ""
    cls = "sec first" if first else "sec"
    md(f'<div class="{cls}">{k}<div class="sec-t">{title}</div>{n}</div>')


def note(text: str):
    md(f'<div class="note">{text}</div>')


def show_all(keys, key: str, limit: int):
    keys = list(keys)
    shown = keys if st.session_state.get(key) else keys[:limit]
    return shown, len(keys) > limit


def film_rows(keys, key: str, sub=None, meta=None, limit: int = 10):
    shown, more = show_all(keys, key, limit)
    rows = []
    for k in shown:
        s = f'<div class="row-s">{sub(k)}</div>' if sub and sub(k) else ""
        m = f'<div class="row-m">{meta(k)}</div>' if meta else ""
        rows.append(f'<div class="row">{poster(k, "sm")}<div class="row-body"><div class="row-t">{title_link(k)}'
                    f'<span class="row-y">{year_str(cat.at[k, "Year"])}</span></div>{s}</div>{m}</div>')
    md("".join(rows))
    if more:
        st.toggle(f"Show all {len(keys)}", key=key)


def poster_wall(keys, key: str, cap=None, limit: int = 9):
    shown, more = show_all(keys, key, limit)
    items = "".join(
        f'<div class="wall-item">{poster(k)}'
        + (f'<div class="wall-cap">{cap(k)}</div>' if cap and cap(k) else "")
        + "</div>"
        for k in shown
    )
    md(f'<div class="wall">{items}</div>')
    if more:
        st.toggle(f"Show all {len(keys)}", key=key)


def marquee(kicker: str, title: str, sub: str):
    md(f'<div class="marquee"><div class="marquee-in"><div class="mq-kick">{kicker}</div>'
       f'<div class="mq-title">{title}</div><div class="mq-sub">{sub}</div></div></div>')


# ---------- Tickets ----------

def ticket(k, name, year, uri, why_html: str, kicker: str = "Tonight's feature", flip: str = "a") -> str:
    link = (f'<a class="tk-link" href="{esc(str(uri))}" target="_blank">Open on Letterboxd →</a>'
            if uri and pd.notna(uri) else "")
    serial = f"No. {zlib.crc32(str(k).encode()) % 900000 + 100000}"
    return (f'<div class="ticket {flip}"><div class="tk-stub"><span>Admit two</span><small>{serial}</small></div>'
            f'<div class="tk-main"><div class="tk-kick">{kicker}</div>'
            f'<div class="tk-body">{poster_html(k, name, year, uri, "lg", link=False)}<div style="min-width:0">'
            f'<div class="tk-title">{esc(str(name))}</div><div class="tk-year">{esc(str(year or ""))}</div>'
            f'<div class="tk-why">{why_html}</div></div></div>{link}</div></div>')


# ---------- Swipe night: shared rooms so two phones can swipe on the same deck ----------
# Rooms live in server memory (one dict shared by every session), keyed by a 4-letter code.
# Nothing personal is stored: just the two display names, the film list and the yes/no votes.

ROOM_TTL = 12 * 3600
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DECK_MAX = 60


@st.cache_resource
def _room_store():
    return {"lock": threading.Lock(), "rooms": {}}


def get_room(code):
    if not code:
        return None
    room = _room_store()["rooms"].get(str(code).strip().upper())
    if room and time.time() - room["created"] > ROOM_TTL:
        return None
    return room


def _new_code(store) -> str:
    """Clear out expired rooms and return an unused 4-letter code. Call with the lock held."""
    now = time.time()
    for c in [c for c, r in store["rooms"].items() if now - r["created"] > ROOM_TTL]:
        del store["rooms"][c]
    code = "".join(random.choices(CODE_LETTERS, k=4))
    while code in store["rooms"]:
        code = "".join(random.choices(CODE_LETTERS, k=4))
    return code


def create_room(names, deck) -> str:
    store = _room_store()
    with store["lock"]:
        code = _new_code(store)
        random.Random(code).shuffle(deck)  # same order on both phones
        store["rooms"][code] = {
            "code": code, "kind": "swipe", "created": time.time(), "names": list(names), "deck": deck[:DECK_MAX],
            "full_deck": deck[:DECK_MAX], "votes": {n: {} for n in names}, "joined": set(),
            "match": None, "passed": set(), "round": 1,
        }
    return code


def cast_vote(code, seat, key, like):
    store = _room_store()
    with store["lock"]:
        room = get_room(code)
        if not room or seat not in room["votes"] or room["match"]:
            return
        if key not in {c["key"] for c in room["deck"]}:
            return
        room["votes"][seat][key] = like
        if like and all(v.get(key) for v in room["votes"].values()):
            room["match"] = key


def new_round(code, keys=None):
    """Start again, either with a subset of the deck (the 'maybe' pile) or the whole thing."""
    store = _room_store()
    with store["lock"]:
        room = get_room(code)
        if not room:
            return
        pool_ = room["full_deck"]
        room["deck"] = [c for c in pool_ if c["key"] in keys] if keys else list(pool_)
        room["votes"] = {n: {} for n in room["names"]}
        room["match"], room["passed"] = None, set()
        room["round"] += 1


def keep_swiping(code):
    store = _room_store()
    with store["lock"]:
        room = get_room(code)
        if room and room["match"]:
            room["passed"].add(room["match"])
            room["match"] = None


def room_state(room, seat):
    """What both phones need to agree on. When this changes, the other phone reloads."""
    other = next(n for n in room["names"] if n != seat)
    n = len(room["deck"])
    return (room["match"], room["round"], len(room["votes"][other]) >= n, other in room["joined"])


SWIPE_JS = """
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  let root = parentElement.querySelector('.sw-root');
  if (!root) {
    root = document.createElement('div'); root.className = 'sw-root'; parentElement.appendChild(root);
    // first time the deck appears, scroll so the card and buttons fill the screen
    setTimeout(() => root.scrollIntoView({ behavior: 'smooth', block: 'end' }), 350);
  }
  root.innerHTML = data.html;
  const card = root.querySelector('.sw-card.top');
  if (!card) return;
  const key = card.dataset.key;
  const yes = card.querySelector('.stamp-yes'), no = card.querySelector('.stamp-no');
  let sx = 0, sy = 0, dx = 0, dy = 0, dragging = false, done = false;
  const send = (like) => {
    if (done) return; done = true;
    card.style.transition = 'transform .38s ease-in, opacity .38s ease-in';
    card.style.transform = `translate(${like ? 560 : -560}px, ${dy * 1.5}px) rotate(${like ? 28 : -28}deg)`;
    card.style.opacity = '0';
    (like ? yes : no).style.opacity = 1;
    setTimeout(() => setTriggerValue('swipe', { key, like, t: Date.now() }), 230);
  };
  card.addEventListener('pointerdown', (e) => {
    if (done || e.target.closest('a')) return;
    dragging = true; sx = e.clientX; sy = e.clientY; dx = dy = 0;
    card.setPointerCapture(e.pointerId); card.style.transition = 'none';
  });
  card.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    dx = e.clientX - sx; dy = e.clientY - sy;
    card.style.transform = `translate(${dx}px, ${dy * 0.3}px) rotate(${dx / 16}deg)`;
    yes.style.opacity = Math.max(0, Math.min(1, dx / 90));
    no.style.opacity = Math.max(0, Math.min(1, -dx / 90));
  });
  const end = () => {
    if (!dragging) return; dragging = false;
    if (Math.abs(dx) > 90) { send(dx > 0); return; }
    card.style.transition = 'transform .35s cubic-bezier(.2,.9,.3,1.4)';
    card.style.transform = ''; yes.style.opacity = 0; no.style.opacity = 0;
  };
  card.addEventListener('pointerup', end);
  card.addEventListener('pointercancel', end);
  root.querySelector('.sw-yes').onclick = () => send(true);
  root.querySelector('.sw-no').onclick = () => send(false);
}
"""
swiper = st.components.v2.component("swipe_deck", js=SWIPE_JS, isolate_styles=False)

ICON_X = ('<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2.6" '
          'stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>')
ICON_HEART = ('<svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M12 21s-7.5-4.6-9.6-9.2'
              'C.9 8.1 3 4 6.9 4c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3C21 4 23.1 8.1 21.6 11.8 19.5 16.4 12 21 12 21z"/>'
              '</svg>')


def swipe_card(c, cls: str) -> str:
    art = poster_html(c["key"], c["name"], "", None, "xl", link=False)
    uri = c.get("uri")
    lb = (f'<a class="sw-lb" href="{esc(str(uri))}" target="_blank">Letterboxd ↗</a>' if uri else "")
    return (f'<div class="sw-card {cls}" data-key="{esc(c["key"])}">{art}'
            f'<div class="stamp stamp-yes">Watch</div><div class="stamp stamp-no">Pass</div>'
            f'<div class="sw-info"><div><b>{esc(str(c["year"] or ""))}</b> · {esc(c["why"])}</div>{lb}</div></div>')


def on_swipe(code, seat):
    v = (st.session_state.get(f"deck_{code}") or {}).get("swipe")
    if v and v.get("key"):
        cast_vote(code, seat, v["key"], bool(v.get("like")))


def choose_seat(code, name):
    st.session_state[f"seat_{code}"] = name
    with _room_store()["lock"]:
        room = get_room(code)
        if room:
            room["joined"].add(name)


def leave_room():
    st.session_state.pop("room", None)
    st.query_params.pop("room", None)


@st.fragment(run_every=2)
def room_pulse(code, seat, seen):
    room = get_room(code)
    if not room:
        return
    if room_state(room, seat) != seen:
        st.rerun(scope="app")
    other = next(n for n in room["names"] if n != seat)
    n, total = len(room["votes"][other]), len(room["deck"])
    if other not in room["joined"] and not n:
        status = "hasn't joined yet"
    elif n >= total:
        status = "has finished swiping"
    else:
        status = f"is swiping · {n} of {total}"
    md(f'<div class="sw-status"><span class="live"></span><b style="color:{COLOURS.get(other, SEA)}">'
       f'{esc(other)}</b> {status}</div>')


def swipe_view(code, host=False):
    room = get_room(code)
    if not room:
        note("<b>That swipe session has ended.</b> Sessions last 12 hours, and restarting the app clears them. "
             "Start a new one from the Swipe tab.")
        st.button("OK", on_click=leave_room)
        return
    names = room["names"]
    COLOURS.update({names[0]: GOLD, names[1]: SEA})
    seat = st.session_state.get(f"seat_{code}")

    if host and len(room["joined"]) < 2:
        link = f"{st.context.url.split('?')[0]}?room={code}" if st.context.url else ""
        md(f'<div class="room"><div class="room-k">Room code</div><div class="room-code">{code}</div>'
           f'<div class="room-n">Open Double Feature on the other phone and enter this code, '
           f'or send this link:</div></div>')
        if link:
            st.code(link, language=None)

    if seat not in names:
        section("Who's on this phone?", kicker=f"Room {code}", first=not host)
        cols = st.columns(2)
        for col, n in zip(cols, names):
            taken = n in room["joined"]
            col.button(f"I'm {n}" + (" (joined)" if taken else ""), key=f"seat_btn_{n}", width="stretch",
                       on_click=choose_seat, args=(code, n))
        return

    other = next(n for n in names if n != seat)
    mine = room["votes"][seat]
    match = room["match"]
    room_pulse(code, seat, room_state(room, seat))

    if match:
        c = next(c for c in room["deck"] if c["key"] == match)
        md(f'<div class="its-match"><div class="im-k">✦ It\'s a match ✦</div>'
           f'<div class="im-t">{esc(names[0])} <em>&amp;</em> {esc(names[1])}</div>'
           f'<div class="im-n">You both swiped right. Tonight\'s film is…</div></div>')
        md(ticket(c["key"], c["name"], c["year"], c["uri"], esc(c["why"]), kicker="Matched for tonight"))
        st.button("Keep swiping for another", width="stretch", on_click=keep_swiping, args=(code,))
        return

    deck = [c for c in room["deck"] if c["key"] not in room["passed"]]
    todo = [c for c in deck if c["key"] not in mine]
    if todo:
        done_n = len(deck) - len(todo)
        md(f'<div class="sw-head"><span>{dot(seat)}Swiping as <b>{esc(seat)}</b></span>'
           f'<span>{done_n + 1} / {len(deck)}</span></div>'
           f'<div class="sw-prog"><i style="width:{100 * done_n / max(len(deck), 1):.1f}%"></i></div>')
        cards = swipe_card(todo[0], "top") + (swipe_card(todo[1], "next") if len(todo) > 1 else "")
        html_ = (f'<div class="sw-stack">{cards}</div><div class="sw-btns">'
                 f'<button class="sw-btn sw-no" aria-label="Pass">{ICON_X}</button>'
                 f'<button class="sw-btn sw-yes" aria-label="Watch">{ICON_HEART}</button></div>'
                 f'<div class="sw-hint">Swipe right to watch, left to pass</div>')
        swiper(key=f"deck_{code}", data={"html": html_}, on_swipe_change=lambda: on_swipe(code, seat))
        return

    theirs = room["votes"][other]
    if len(theirs) < len(room["deck"]):
        note(f"<b>You're done.</b> Waiting for {esc(other)} to finish. "
             f"If you both like the same film, it'll pop up here.")
        return
    maybes = [c["key"] for c in deck if mine.get(c["key"]) or theirs.get(c["key"])]
    if not maybes:
        msg = "You passed on everything. Tough crowd."
    elif len(maybes) < len(deck):
        msg = f"{len(maybes)} films got a yes from one of you. Swipe on just those to settle it?"
    else:
        msg = "Every film got a yes from one of you, just never both at once. Go again?"
    note(f"<b>No match this round.</b> {msg}")
    if maybes and len(maybes) < len(deck):
        st.button("Swipe the maybes", type="primary", width="stretch", on_click=new_round, args=(code, maybes))
    st.button("Start over with the whole deck", width="stretch", on_click=new_round, args=(code,))


SWIPE_CSS = """
<style>
.room { text-align: center; border-radius: 18px; padding: 1rem 1rem .9rem; margin: .4rem 0 .5rem;
  background: linear-gradient(160deg, rgba(242,193,78,.14), rgba(142,197,192,.08));
  box-shadow: inset 0 0 0 1px rgba(242,193,78,.3); }
.room-k { font-family: var(--label); letter-spacing: .26em; font-size: .85rem; color: var(--gold); }
.room-code { font-family: var(--label); font-size: 3.6rem; letter-spacing: .3em; line-height: 1; padding: .3rem 0 .2rem .3em;
  color: var(--cream); text-shadow: 0 0 24px rgba(242,193,78,.35); }
.room-n { color: var(--muted); font-size: .88rem; line-height: 1.45; }
.sw-head { display: flex; justify-content: space-between; align-items: center; font-size: .88rem; color: var(--muted);
  margin: .5rem 0 .45rem; }
.sw-head b { color: var(--cream); font-weight: 600; }
.sw-head span:last-child { font-family: var(--label); letter-spacing: .14em; font-size: .95rem; }
.sw-prog { height: 3px; border-radius: 3px; background: rgba(243,236,221,.08); overflow: hidden; margin-bottom: .9rem; }
.sw-prog i { display: block; height: 100%; background: linear-gradient(90deg, var(--gold), var(--sea)); }
.sw-stack { position: relative; height: min(450px, calc(100svh - 250px), 120vw); aspect-ratio: 2 / 3; margin: 0 auto; }
.sw-card { position: absolute; inset: 0; border-radius: 18px; overflow: hidden; touch-action: pan-y; user-select: none;
  -webkit-user-select: none; cursor: grab; will-change: transform;
  box-shadow: 0 24px 50px -20px rgba(0,0,0,.9), 0 0 0 1px rgba(255,255,255,.06); }
.sw-card.top { z-index: 2; animation: sw-in .3s ease-out both; }
.sw-card.next { z-index: 1; transform: translateY(12px) scale(.94); filter: brightness(.55); pointer-events: none; }
@keyframes sw-in { from { transform: scale(.96); } }
.sw-card .poster { position: absolute; inset: 0; width: 100%; height: 100%; aspect-ratio: auto; border-radius: 0; }
.p-xl .p-t { font-size: 2.05rem; -webkit-line-clamp: 5; padding: 11% 10% 0; line-height: 1.02; }
.p-xl .p-y { display: none; }
.sw-info { position: absolute; left: 0; right: 0; bottom: 0; padding: 3.2rem 1.1rem 1rem; z-index: 3;
  display: flex; justify-content: space-between; align-items: flex-end; gap: .6rem;
  background: linear-gradient(180deg, rgba(10,14,22,0), rgba(10,14,22,.88)); font-size: .86rem; color: var(--cream); }
.sw-info b { font-family: var(--label); letter-spacing: .12em; font-weight: 400; font-size: 1rem; }
.sw-lb { flex: 0 0 auto; font-family: var(--label); letter-spacing: .12em; font-size: .85rem; color: var(--gold) !important;
  text-decoration: none !important; padding: .3rem .6rem .15rem; border-radius: 999px; background: rgba(17,25,38,.7); }
.stamp { position: absolute; top: 42%; z-index: 4; font-family: var(--label); font-size: 2.3rem; letter-spacing: .14em;
  padding: .25rem .7rem 0; border: 4px solid; border-radius: 10px; opacity: 0; pointer-events: none;
  background: rgba(17,25,38,.35); }
.stamp-yes { left: 1rem; color: var(--sea); border-color: var(--sea); transform: rotate(-14deg); }
.stamp-no { right: 1rem; color: var(--coral); border-color: var(--coral); transform: rotate(14deg); }
.sw-btns { display: flex; justify-content: center; gap: 2rem; margin: 1.3rem 0 .4rem; }
.sw-btn { width: 66px; height: 66px; border-radius: 50%; border: 0; display: grid; place-items: center; cursor: pointer;
  transition: transform .15s; -webkit-tap-highlight-color: transparent; }
.sw-btn:active { transform: scale(.9); }
.sw-no { background: rgba(238,138,109,.1); color: var(--coral); box-shadow: inset 0 0 0 2px rgba(238,138,109,.55); }
.sw-yes { background: linear-gradient(180deg, #F7D06E, var(--gold) 55%, var(--gold-deep)); color: var(--ink);
  box-shadow: 0 10px 26px -8px rgba(242,193,78,.7); }
.sw-hint { text-align: center; color: var(--muted); font-size: .8rem; margin-bottom: .6rem; }
.sw-status { display: flex; align-items: center; justify-content: center; gap: .1rem; font-size: .85rem;
  color: var(--muted); padding: .45rem .8rem; border-radius: 999px; background: rgba(27,37,54,.75);
  box-shadow: inset 0 0 0 1px var(--line); width: fit-content; margin: .3rem auto .6rem; }
.sw-status b { color: var(--cream); font-weight: 600; margin-right: .3rem; }
.live { width: .45rem; height: .45rem; border-radius: 50%; background: #6FD08C; margin-right: .5rem;
  box-shadow: 0 0 0 0 rgba(111,208,140,.6); animation: live 1.8s infinite; }
@keyframes live { 70% { box-shadow: 0 0 0 7px rgba(111,208,140,0); } 100% { box-shadow: 0 0 0 0 rgba(111,208,140,0); } }
.its-match { text-align: center; margin: .6rem 0 1rem; animation: pop .6s cubic-bezier(.2,.9,.25,1.3) both; }
@keyframes pop { from { opacity: 0; transform: scale(.85); } }
.im-k { font-family: var(--label); letter-spacing: .3em; color: var(--gold); font-size: 1rem; }
.im-t { font-family: var(--display); font-size: 2.3rem; font-weight: 600; line-height: 1.05; margin: .25rem 0 .3rem;
  text-shadow: 0 0 30px rgba(242,193,78,.35); }
.im-t em { color: var(--gold); font-weight: 400; }
.im-n { color: var(--muted); font-size: .92rem; }
.how { display: grid; gap: 8px; margin: .8rem 0 1.1rem; }
.how div { display: flex; gap: .8rem; align-items: center; border-radius: 14px; padding: .75rem .9rem;
  background: rgba(27,37,54,.75); box-shadow: inset 0 0 0 1px var(--line); font-size: .9rem; line-height: 1.4; }
.how b { flex: 0 0 1.9rem; height: 1.9rem; border-radius: 50%; display: grid; place-items: center; font-family: var(--label);
  font-size: 1.05rem; font-weight: 400; background: rgba(242,193,78,.15); color: var(--gold); padding-top: .1rem; }
@media (prefers-reduced-motion: reduce) { .sw-card.top, .its-match, .live { animation: none !important; } }
</style>
"""
md(SWIPE_CSS)


# ---------- Uploads (or joining someone else's swipe session) ----------

def bundle(files):
    """Turn an upload into zip bytes: the export zip itself, or loose CSVs from an unzipped export."""
    files = files or []
    for f in files:
        if f.name.lower().endswith(".zip"):
            return f.getvalue()
    csvs = [f for f in files if f.name.lower().endswith(".csv")]
    if not csvs:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in csvs:
            z.writestr(f.name.rsplit("/", 1)[-1].lower(), f.getvalue())
    return buf.getvalue()


# ---------- Pairing: each person uploads their own export on their own phone ----------
# A pair room holds both exports in server memory (never on disk) for up to 12 hours,
# so either phone can load the full app. Swipe sessions started while paired live inside it.

def create_pair(name, raw) -> str:
    store = _room_store()
    with store["lock"]:
        code = _new_code(store)
        store["rooms"][code] = {"code": code, "kind": "pair", "created": time.time(),
                                "slots": [{"name": name, "raw": raw}, None], "swipe": None}
    return code


def pair_state(room):
    """When this changes, the other phone reloads (partner arrived, swipe session started or ended)."""
    return (all(room["slots"]), room["swipe"])


def read_upload(files, typed_name: str, fallback: str):
    """Validate one person's upload. Returns (raw, name) or (None, error message)."""
    raw = bundle(files)
    try:
        d = load_export(raw) if raw else None
    except (zipfile.BadZipFile, TypeError, pd.errors.ParserError):
        d = None
    if not d or not d["found"]:
        return None, ("That doesn't look like a Letterboxd export. Upload the .zip from letterboxd.com → "
                      "Settings → Data → Export your data, or the watched.csv, ratings.csv and watchlist.csv inside it.")
    return raw, (typed_name.strip() or d["name"] or fallback)


def start_pair():
    raw, name = read_upload(st.session_state.get("sp_file"), st.session_state.get("sp_name", ""), "Person 1")
    if raw is None:
        st.session_state.sp_error = name
        return
    st.session_state.sp_error = ""
    st.session_state.pair = create_pair(name, raw)
    st.session_state.me = 0


def finish_pair(code):
    raw, name = read_upload(st.session_state.get("pj_file"), st.session_state.get("pj_name", ""), "Person 2")
    if raw is None:
        st.session_state.pj_error = name
        return
    store = _room_store()
    with store["lock"]:
        room = get_room(code)
        if not room or room["slots"][1] is not None:
            st.session_state.pj_error = "Someone has already paired with this code."
            return
        if name.casefold() == room["slots"][0]["name"].casefold():
            name = f"{name} (2)"
        room["slots"][1] = {"name": name, "raw": raw}
    st.session_state.pj_error = ""
    st.session_state.pair, st.session_state.me = code, 1
    st.session_state.pop("pair_join", None)


def rejoin_pair(code, i):
    st.session_state.pair, st.session_state.me = code, i
    st.session_state.pop("pair_join", None)


def unpair():
    for k in ("pair", "me", "pair_join", "seen_swipe", "room"):
        st.session_state.pop(k, None)
    st.query_params.pop("room", None)


def route_code(code: str) -> str:
    """Send a typed or linked code to the right place. Returns an error message, or ''."""
    code = (code or "").strip().upper()
    room = get_room(code)
    if not room:
        return f"No session called {code or '…'}. Check the code on the other phone."
    if room["kind"] == "pair":
        st.session_state.pair_join = code
    else:
        st.session_state.room = code
    return ""


def join_room():
    st.session_state.join_error = route_code(st.session_state.get("join_code", ""))


def start_pair_swipe(pair_code, names, deck, seat):
    sc = create_room(names, deck)
    store = _room_store()
    with store["lock"]:
        room = get_room(pair_code)
        if room:
            room["swipe"] = sc
    st.session_state.seen_swipe = sc
    choose_seat(sc, seat)


def end_pair_swipe(pair_code):
    with _room_store()["lock"]:
        room = get_room(pair_code)
        if room:
            room["swipe"] = None


@st.fragment(run_every=2)
def pair_pulse(code, seen):
    room = get_room(code)
    if not room or pair_state(room) != seen:
        st.rerun(scope="app")


def code_card(code, text):
    link = f"{st.context.url.split('?')[0]}?room={code}" if st.context.url else ""
    md(f'<div class="room"><div class="room-k">Pair code</div><div class="room-code">{code}</div>'
       f'<div class="room-n">{text}</div></div>')
    if link:
        st.code(link, language=None)


def join_box():
    section("Got a code?", kicker="Joining someone",
            note="If the other person already started on their phone, enter their code here.")
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    c1.text_input("Code", key="join_code", max_chars=4, placeholder="ABCD")
    c2.button("Join", width="stretch", on_click=join_room)
    if st.session_state.get("join_error"):
        st.caption(st.session_state.join_error)


UPLOAD_HELP = ("On letterboxd.com (not the app) go to Settings → Data → Export your data, then upload "
               "the .zip. If it got unzipped, pick watched.csv, ratings.csv and watchlist.csv instead.")
NAME_HINT = "Name (optional, otherwise we use Letterboxd's)"

header = st.container()
have_both = all(st.session_state.get(k) for k in ("file_a", "file_b"))

# A shared link (?room=CODE) works once per page load, for pair codes and swipe codes alike
qp = (st.query_params.get("room") or "").strip().upper()
if qp and st.session_state.get("qp_handled") != qp:
    st.session_state.qp_handled = qp
    if not st.session_state.get("pair"):
        st.session_state.join_error = route_code(qp)

pair_code, me = st.session_state.get("pair"), st.session_state.get("me")
pair = get_room(pair_code) if pair_code else None
if pair_code and not pair:
    unpair()
    st.session_state.join_error = "Your pairing has expired (they last 12 hours). Start a new one below."
paired = bool(pair and all(pair["slots"]) and me is not None)

# 1. Started a pair, waiting for the other person to upload
if pair and not paired:
    with header:
        marquee("Pairing up", f"{esc(pair['slots'][0]['name'])} <em>&amp;</em> …",
                "Waiting for your plus-one")
    code_card(pair_code, "Send this link, or have them enter the code in Double Feature. They upload their own "
                         "export and both phones open up together.")
    md('<div class="sw-status"><span class="live"></span>Waiting for the other person to upload</div>')
    pair_pulse(pair_code, pair_state(pair))
    st.button("Cancel", on_click=unpair)
    st.stop()

# 2. Joining someone else's pair code
if not paired and st.session_state.get("pair_join"):
    jc = st.session_state.pair_join
    room = get_room(jc)
    with header:
        if room:
            marquee("Pairing up", f"{esc(room['slots'][0]['name'])} <em>&amp;</em> you",
                    f"Pair code <b>{jc}</b>")
        else:
            marquee("Pairing up", "Double <em>Feature</em>", "Two phones<i>✦</i>One movie night")
    if not room:
        note("<b>That code has expired.</b> Ask for a new one.")
        st.button("Back", on_click=unpair)
    elif room["slots"][1] is None:
        note(f"<b>{esc(room['slots'][0]['name'])} wants to pair up.</b> Add your own Letterboxd export and "
             "you'll both get the full app on your own phones.")
        st.caption(UPLOAD_HELP)
        st.text_input("Your name", key="pj_name", placeholder=NAME_HINT)
        st.file_uploader("Your Letterboxd export", type=["zip", "csv"], accept_multiple_files=True, key="pj_file")
        st.button("Pair up", type="primary", width="stretch", on_click=finish_pair, args=(jc,),
                  disabled=not st.session_state.get("pj_file"))
        if st.session_state.get("pj_error"):
            st.error(st.session_state.pj_error)
        st.button("Cancel", on_click=unpair)
    else:
        section("Which one are you?", kicker=f"Pair {jc}", first=True,
                note="This pair is already set up. Pick your name to open it on this phone.")
        cols = st.columns(2)
        for i, (col, slot) in enumerate(zip(cols, room["slots"])):
            col.button(slot["name"], key=f"rejoin_{i}", width="stretch", on_click=rejoin_pair, args=(jc, i))
    st.stop()

# 3. Joining a swipe session on a phone with no exports
if not paired and not have_both and st.session_state.get("room"):
    room = get_room(st.session_state.room)
    with header:
        if room:
            marquee("Swipe night", f"{esc(room['names'][0])} <em>&amp;</em> {esc(room['names'][1])}",
                    f"Room <b>{room['code']}</b><i>✦</i><b>{len(room['deck'])}</b> films in the deck")
        else:
            marquee("Swipe night", "Double <em>Feature</em>", "Two phones<i>✦</i>One film")
    swipe_view(st.session_state.room)
    if room:
        st.button("Leave this session", on_click=leave_room)
    st.stop()

if paired:
    s0, s1 = pair["slots"]
    raw_a, raw_b, name_a, name_b = s0["raw"], s1["raw"], s0["name"], s1["name"]
    with st.expander(f"Paired on two phones · {pair_code}"):
        st.caption(f"This phone is {[name_a, name_b][me]}'s. Both exports are held in the app's memory "
                   "for up to 12 hours so each phone can load them, and never saved to disk.")
        st.button("Unpair this phone", on_click=unpair)
    pair_pulse(pair_code, pair_state(pair))
else:
    mode = "one" if have_both else st.segmented_control(
        "How are you doing this?", ["own", "one"], default="own", key="mode", label_visibility="collapsed",
        format_func={"own": "Each on our own phone", "one": "Both on this phone"}.get) or "own"
    if not have_both:
        with header:
            marquee("Now showing", "Double <em>Feature</em>", "Two Letterboxd accounts<i>✦</i>One movie night")
            note("<b>Here's the plan.</b> Add both of your Letterboxd exports and you'll get a film to watch "
                 "tonight, a taste-match score, a swipe-to-match game and a list of what to show each other.")

    if mode == "own":
        section("Start here", kicker="Each on your own phone", first=True,
                note="Upload your own export and you'll get a code to send to the other person. "
                     "Nobody needs to log in to anyone else's Letterboxd.")
        st.caption(UPLOAD_HELP)
        st.text_input("Your name", key="sp_name", placeholder=NAME_HINT)
        st.file_uploader("Your Letterboxd export", type=["zip", "csv"], accept_multiple_files=True, key="sp_file")
        st.button("Get a pair code", type="primary", width="stretch", on_click=start_pair,
                  disabled=not st.session_state.get("sp_file"))
        if st.session_state.get("sp_error"):
            st.error(st.session_state.sp_error)
        join_box()
        st.stop()

    with st.expander("Your Letterboxd exports", expanded=not have_both):
        st.caption(UPLOAD_HELP + " Files stay in this session and aren't saved.")
        name_a = st.text_input("First person", placeholder=NAME_HINT)
        file_a = st.file_uploader("First person's export", type=["zip", "csv"],
                                  accept_multiple_files=True, key="file_a")
        st.divider()
        name_b = st.text_input("Second person", placeholder=NAME_HINT)
        file_b = st.file_uploader("Second person's export", type=["zip", "csv"],
                                  accept_multiple_files=True, key="file_b")
    if not (file_a and file_b):
        join_box()
        st.stop()
    raw_a, raw_b = bundle(file_a), bundle(file_b)

try:
    da, db = load_export(raw_a), load_export(raw_b)
except (zipfile.BadZipFile, TypeError, pd.errors.ParserError):
    da = db = None
bad = [label for label, d in (("the first person", da), ("the second person", db)) if not d or not d["found"]]
if bad:
    st.error(
        f"Couldn't read {' or '.join(bad)}'s export. Upload the .zip from letterboxd.com → Settings → Data "
        "→ Export your data, or the watched.csv, ratings.csv and watchlist.csv files inside it."
    )
    st.stop()

# Names: whatever was typed, else the name on the Letterboxd profile, else a placeholder
A = name_a.strip() or da["name"] or "Person 1"
B = name_b.strip() or db["name"] or "Person 2"
if A.casefold() == B.casefold():
    B = f"{B} (2)"
COLOURS.update({A: GOLD, B: SEA})

# Paired: let this phone know when the other one starts a swipe session
if paired and pair["swipe"] and pair["swipe"] != st.session_state.get("seen_swipe"):
    st.session_state.seen_swipe = pair["swipe"]
    st.toast(f"{[A, B][1 - me]} started a swipe session. Open the Swipe tab to join in.")

pa, pb = summarise(da), summarise(db)
cat = build_catalog(da, db)
shared_wl = pa["watchlist"] & pb["watchlist"]
both_seen = pa["seen"] & pb["seen"]

with header:
    marquee("Now showing", f"{esc(A)} <em>&amp;</em> {esc(B)}",
            f"<b>{len(both_seen)}</b> seen together<i>✦</i><b>{len(shared_wl)}</b> on both watchlists")

t_pick, t_swipe, t_taste, t_swap, t_stats = st.tabs(["Pick", "Swipe", "Taste", "Swaps", "Stats"])


# ---------- Pick ----------

def pick_film(options):
    st.session_state.pick = random.choice(options)
    st.session_state.picks = st.session_state.get("picks", 0) + 1


def reason_text(why, on_ticket=False) -> str:
    if why is None:
        return "On both watchlists"
    name, r = why
    return f"{esc(name)} gave it {star_bar(r, None if on_ticket else COLOURS.get(name))}"


def reason_plain(why) -> str:
    return "On both watchlists" if why is None else f"{why[0]} gave it {stars(why[1])}"


with t_pick:
    filters = st.expander("Filters")
    widen = filters.toggle("Add films one of you rated 4★+ that the other wants to see")
    pool = dict.fromkeys(shared_wl, None)
    if widen:
        for k in pa["watchlist"]:
            if pb["ratings"].get(k, 0) >= 4:
                pool.setdefault(k, (B, pb["ratings"][k]))
        for k in pb["watchlist"]:
            if pa["ratings"].get(k, 0) >= 4:
                pool.setdefault(k, (A, pa["ratings"][k]))

    decades = sorted({int(y) // 10 * 10 for y in cat.loc[list(pool), "Year"].dropna()})
    chosen = filters.pills("Decades", decades, selection_mode="multi",
                           format_func=lambda d: f"{str(d)[2:]}s") if decades else []
    if chosen:
        pool = {k: why for k, why in pool.items()
                if pd.notna(cat.at[k, "Year"]) and int(cat.at[k, "Year"]) // 10 * 10 in chosen}

    if not pool:
        note("<b>Nothing in the pool.</b> Open Filters to add films one of you loved, or clear the decades.")
    else:
        pick = st.session_state.get("pick")
        if pick in pool:
            r = cat.loc[pick]
            flip = "a" if st.session_state.get("picks", 0) % 2 else "b"
            md(ticket(pick, r["Name"], year_str(r["Year"]), r["Letterboxd URI"],
                      reason_text(pool[pick], on_ticket=True), flip=flip))
        else:
            md(f'<div class="tk-ghost"><b>What are we watching?</b>{len(pool)} films in the hat. '
               f'Let fate decide.</div>')
        st.button("Pick another" if pick in pool else "Pick tonight's film", type="primary",
                  width="stretch", on_click=pick_film, args=(sorted(pool),))

        section("The pool", kicker=f"{len(pool)} films in the hat")
        poster_wall(sorted(pool, key=lambda k: str(cat.at[k, "Name"]).casefold()), key="all_pool",
                    cap=lambda k: reason_text(pool[k]) if pool[k] else "")


# ---------- Swipe ----------

def start_room(names, deck):
    st.session_state.room = create_room(names, deck)


with t_swipe:
    code = st.session_state.get("room")
    sc = pair["swipe"] if paired else None
    deck = [{"key": k, "name": str(cat.at[k, "Name"]), "year": year_str(cat.at[k, "Year"]),
             "uri": str(cat.at[k, "Letterboxd URI"]) if pd.notna(cat.at[k, "Letterboxd URI"]) else "",
             "why": reason_plain(why)} for k, why in pool.items()]
    if paired and sc and get_room(sc):
        seat = [A, B][me]
        if st.session_state.get(f"seat_{sc}") != seat:
            choose_seat(sc, seat)
        swipe_view(sc)
        st.button("End swipe session", on_click=end_pair_swipe, args=(pair_code,))
    elif paired:
        other = [A, B][1 - me]
        section("Swipe to match", kicker="Swipe night", first=True,
                note="Like a dating app, but for films. The first film you both swipe right on is the one.")
        md(f'<div class="how"><div><b>1</b>Start a session. You\'re already paired, so there\'s no code: '
           f'{esc(other)} gets the same deck on their phone.</div>'
           '<div><b>2</b>You each swipe through the same films. Right to watch, left to pass.</div>'
           '<div><b>3</b>The first film you both like pops up on both phones.</div></div>')
        if not pool:
            note("The deck uses the films in the Pick tab, and it's empty right now. "
                 "Open Filters there to add more.")
        else:
            st.button(f"Start swiping · {min(len(deck), DECK_MAX)} films", type="primary", width="stretch",
                      on_click=start_pair_swipe, args=(pair_code, [A, B], deck, [A, B][me]))
            st.caption("The deck is the Pick tab's pool, including any filters you've set there.")
    elif code and get_room(code):
        swipe_view(code, host=True)
        st.button("End swipe session", on_click=leave_room)
    else:
        section("Swipe to match", kicker="Swipe night", first=True,
                note="Like a dating app, but for films. The first film you both swipe right on is the one.")
        md('<div class="how"><div><b>1</b>Start a session here and you get a four-letter room code.</div>'
           '<div><b>2</b>The other person opens Double Feature on their phone and enters it. '
           'They don\'t need to upload anything.</div>'
           '<div><b>3</b>You each swipe through the same films. Right to watch, left to pass.</div></div>')
        if not pool:
            note("The deck uses the films in the Pick tab, and it's empty right now. "
                 "Open Filters there to add more.")
        else:
            n = min(len(deck), DECK_MAX)
            st.button(f"Start a swipe session · {n} films", type="primary", width="stretch",
                      on_click=start_room, args=([A, B], deck))
            st.caption("The deck is the Pick tab's pool, including any filters you've set there.")


# ---------- Taste ----------

VERDICTS = [
    (88, "Same brain, two accounts", "Are you sure these aren't the same person?"),
    (78, "Cinematic soulmates", "You'll rarely fight over the remote."),
    (68, "Popcorn-compatible", "Mostly in sync, with the odd spicy take."),
    (58, "Healthy debate", "Plenty to talk about on the way home."),
    (0, "Opposites attract", "At least the post-film chats will be lively."),
]

with t_taste:
    common = pa["ratings"].index.intersection(pb["ratings"].index)
    if len(common) < 3:
        note(f"<b>Not enough overlap yet.</b> You've both rated {len(common)} of the same films. "
             "Once there are at least 3, this shows how your tastes compare.")
    else:
        ra, rb = pa["ratings"][common], pb["ratings"][common]
        gap = (ra - rb).abs()
        score = round(100 * (1 - gap.mean() / 4.5))
        lean = ra.mean() - rb.mean()
        tougher = "Even" if abs(lean) < 0.1 else (B if lean > 0 else A)
        verdict, verdict_note = next((v, n) for cut, v, n in VERDICTS if score >= cut)
        md(f'<div class="match"><div class="ring" style="--p:{score}"><div><b>{score}<small>%</small></b>'
           f'<span>Taste match</span></div></div><div class="verdict">{verdict}</div>'
           f'<div class="verdict-n">{verdict_note}</div></div>'
           f'<div class="trio"><div><b>{len(common)}</b><span>Both rated</span></div>'
           f'<div><b>{gap.mean():.1f}★</b><span>Average gap</span></div>'
           f'<div><b>{esc(tougher)}</b><span>Tougher critic</span></div></div>')

        section("Side by side", kicker="Every shared rating",
                note=f'Each dot is a film. <span style="color:{GOLD}">Gold</span>: {esc(A)} rated it higher. '
                     f'<span style="color:{SEA}">Green</span>: {esc(B)} did. On the line: you agreed.')
        rng = random.Random(7)
        plot = pd.DataFrame({"Film": cat.loc[common, "Name"].values, "a": ra.values, "b": rb.values})
        plot["aj"] = plot["a"] + [rng.uniform(-0.13, 0.13) for _ in range(len(plot))]
        plot["bj"] = plot["b"] + [rng.uniform(-0.13, 0.13) for _ in range(len(plot))]
        plot["Higher"] = ["Agreed" if x == y else (A if x > y else B) for x, y in zip(plot["a"], plot["b"])]
        scale = alt.Scale(domain=[0.25, 5.25], nice=False)
        ax = dict(values=[1, 2, 3, 4, 5], labelExpr="datum.value + '★'")
        points = alt.Chart(plot).mark_circle(size=110, opacity=0.85, stroke="#111926", strokeWidth=1).encode(
            x=alt.X("aj:Q", title=f"{A} →", scale=scale, axis=alt.Axis(**ax)),
            y=alt.Y("bj:Q", title=f"{B} →", scale=scale, axis=alt.Axis(**ax)),
            color=alt.Color("Higher:N", scale=alt.Scale(domain=[A, B, "Agreed"], range=[GOLD, SEA, CREAM]),
                            legend=None),
            tooltip=["Film", alt.Tooltip("a:Q", title=A), alt.Tooltip("b:Q", title=B)],
        )
        diagonal = alt.Chart(pd.DataFrame({"aj": [0.25, 5.25], "bj": [0.25, 5.25]})).mark_line(
            strokeDash=[3, 5], color="#5C6A82", strokeWidth=1.5).encode(x="aj:Q", y="bj:Q")
        chart = (diagonal + points).properties(height=330).configure(
            background="transparent", font="DM Sans").configure_view(stroke=None).configure_axis(
            labelColor=MUTED, titleColor=MUTED, gridColor="#ffffff12", domain=False, ticks=False,
            labelFontSize=12, titleFontSize=12, titleFontWeight=500, labelPadding=8, titlePadding=10)
        st.altair_chart(chart, theme=None)

        loved = sorted((k for k in common if ra[k] >= 4.5 and rb[k] >= 4.5), key=lambda k: -(ra[k] + rb[k]))
        section("You both loved", kicker=f"{len(loved)} mutual favourites" if loved else "Mutual favourites",
                note="Films you each gave 4½★ or more.")
        if loved:
            items = "".join(
                f'<div class="reel-item">{poster(k)}<div class="reel-cap">'
                f'{rating_line(A, ra[k])}{rating_line(B, rb[k])}</div></div>' for k in loved)
            md(f'<div class="reel">{items}</div>')
        else:
            note("No films you both rated 4½★ or higher yet. Get watching.")

        fights = gap[gap >= 1.5].sort_values(ascending=False).index.tolist()
        section("Biggest disagreements", kicker="Fighting words",
                note="1½★ or more apart. Settle these over dinner.")
        if fights:
            film_rows(fights, key="all_fights", limit=6,
                      sub=lambda k: rating_line(A, ra[k]) + rating_line(B, rb[k]),
                      meta=lambda k: f'<div class="vs-gap">{gap[k]:g}★<small>apart</small></div>')
        else:
            note("You're never more than a star apart. Suspicious.")


# ---------- Swaps ----------

with t_swap:
    section("Show each other", kicker="Swaps", first=True,
            note="Films one of you rated highly that the other hasn't logged yet.")
    to = st.segmented_control("Recommendations for", [A, B], default=A,
                              format_func=lambda n: f"For {n}", label_visibility="collapsed") or A
    threshold = st.segmented_control("Minimum rating", [3.0, 3.5, 4.0, 4.5, 5.0], default=4.0,
                                     format_func=lambda r: (f"{r:g}★+" if r % 1 == 0 else f"{int(r)}½★+") if r < 5 else "5★") or 4.0
    frm = B if to == A else A
    pto, pfrm = (pa, pb) if to == A else (pb, pa)
    recs = pfrm["ratings"][(pfrm["ratings"] >= threshold) & ~pfrm["ratings"].index.isin(list(pto["seen"]))]
    if recs.empty:
        note(f"<b>All caught up.</b> {esc(to)} has already seen everything {esc(frm)} rated "
             f"{stars(threshold)} or higher.")
    else:
        order = sorted(recs.index, key=lambda k: (-recs[k], str(cat.at[k, "Name"]).casefold()))
        md(f'<div class="sec-n" style="margin:.4rem 0 .2rem">{dot(frm)}<b style="color:var(--cream)">'
           f'{len(order)} picks from {esc(frm)}</b> that {esc(to)} hasn\'t seen</div>')
        film_rows(order, key=f"all_recs_{to}", limit=12,
                  meta=lambda k: star_bar(recs[k], COLOURS[frm]),
                  sub=lambda k: f'<span class="chip">On {esc(to)}\'s watchlist</span>' if k in pto["watchlist"] else "")


# ---------- Stats ----------

def pct_split(x, y) -> str:
    tot = (x or 0) + (y or 0)
    if not tot:
        return '<div class="tape-bar"></div>'
    a = 100 * (x or 0) / tot
    return (f'<div class="tape-bar"><i style="width:{a:.1f}%;background:{GOLD}"></i>'
            f'<i style="flex:1;background:{SEA}"></i></div>')


def fav_decade(p):
    yrs = cat.loc[list(p["seen"]), "Year"].dropna().astype(int)
    return f"{(yrs // 10 * 10).mode().iloc[0]}s" if len(yrs) else "–"


def butterfly(rows, fmt):
    """rows: list of (label_html, value_a, value_b), drawn as bars out from the middle."""
    top = max([max(a, b) for _, a, b in rows] + [1e-9])
    body = "".join(
        f'<div class="fly-r"><div class="fly-a"><i style="width:{68 * a / top:.1f}%"></i>{fmt(a)}</div>'
        f'<div class="fly-l">{lab}</div>'
        f'<div class="fly-b"><i style="width:{68 * b / top:.1f}%"></i>{fmt(b)}</div></div>'
        for lab, a, b in rows)
    return (f'<div class="fly"><div class="fly-h"><span>{esc(A)}</span><span></span><span>{esc(B)}</span></div>'
            f'{body}</div>')


with t_stats:
    avg = lambda p: p["ratings"].mean() if len(p["ratings"]) else None
    fives = lambda p: int((p["ratings"] == 5).sum())
    rows = [
        ("Films logged", len(pa["seen"]), len(pb["seen"]), "{:,}"),
        ("Films rated", len(pa["ratings"]), len(pb["ratings"]), "{:,}"),
        ("Watchlist", len(pa["watchlist"]), len(pb["watchlist"]), "{:,}"),
        ("Five-star films", fives(pa), fives(pb), "{:,}"),
        ("Average rating", avg(pa), avg(pb), "{:.2f}★"),
    ]
    tape = "".join(
        f'<div class="tape-r"><b style="color:{GOLD}">{fmt.format(x) if x is not None else "–"}</b>'
        f'<span>{label}</span><b style="color:{SEA}">{fmt.format(y) if y is not None else "–"}</b>'
        f'{pct_split(x, y)}</div>'
        for label, x, y, fmt in rows)
    tape += (f'<div class="tape-r"><b style="color:{GOLD}">{fav_decade(pa)}</b><span>Favourite decade</span>'
             f'<b style="color:{SEA}">{fav_decade(pb)}</b></div>')
    section("Tale of the tape", kicker="Head to head", first=True)
    md(f'<div class="tape"><div class="tape-h"><span style="color:{GOLD}">{esc(A)}</span><i>vs</i>'
       f'<span style="color:{SEA}">{esc(B)}</span></div>{tape}</div>'
       f'<div class="together"><div><b>{len(both_seen)}</b><span>Seen by both</span></div>'
       f'<div><b>{len(shared_wl)}</b><span>Shared watchlist</span></div></div>')

    def decade_counts(p):
        yrs = cat.loc[list(p["seen"]), "Year"].dropna().astype(int)
        return (yrs // 10 * 10).value_counts()

    da_c, db_c = decade_counts(pa), decade_counts(pb)
    decs = sorted(set(da_c.index) | set(db_c.index), reverse=True)
    if decs:
        section("Through the decades", kicker="When your films are from",
                note="Films each of you has logged, by release decade.")
        md(butterfly([(f"{str(d)[2:]}s" if d >= 1930 else f"{d}s", int(da_c.get(d, 0)), int(db_c.get(d, 0)))
                      for d in decs], fmt=lambda v: f"{v}" if v else ""))

    if len(pa["ratings"]) or len(pb["ratings"]):
        sa = pa["ratings"].value_counts(normalize=True)
        sb = pb["ratings"].value_counts(normalize=True)
        levels = [5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5]
        levels = [lv for lv in levels if sa.get(lv, 0) or sb.get(lv, 0)]
        section("How you hand out stars", kicker="Generosity check",
                note="Share of each person's ratings at every star level.")
        md(butterfly([(star_bar(lv, CREAM), float(sa.get(lv, 0)), float(sb.get(lv, 0))) for lv in levels],
                     fmt=lambda v: f"{v:.0%}" if v else ""))
