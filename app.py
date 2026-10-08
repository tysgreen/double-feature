"""Double Feature: compare two Letterboxd accounts and pick a film together."""

import base64
import concurrent.futures
import functools
import html
import io
import math
import os
import random
import re
import secrets
import string
import threading
import time
import unicodedata
import urllib.parse
import zipfile
import zlib
from pathlib import Path

import altair as alt
import pandas as pd
import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


# Parsed exports are cached so tab switches are instant, but capped so a busy day can't fill memory
@st.cache_data(show_spinner=False, max_entries=100, ttl=12 * 3600)
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
.poster.real .p-img { position: absolute; inset: 0; z-index: 1; background: center / cover no-repeat; }
.poster.real:before { z-index: 2; }
.sw-title { font-family: var(--display); font-size: 1.15rem; font-weight: 600; line-height: 1.15; margin-top: .1rem; }
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

# ---------- Generated poster art ----------
# Every film gets a screen-print style poster. The picture comes from the title first ("Before Sunrise"
# gets a sunrise, "Jaws" a fin in the water), then the film's genre if details are loaded, and otherwise
# one of the abstract designs picked from a hash of the title. Shapes are described once, on a
# 100 x 150 canvas, and drawn both as SVG (in the app) and with Pillow (in share images).

# (top, bottom, motif colour, text colour)
PALETTES = {
    "plum": ("#2B1B3D", "#4A2A5E", "#F2C14E", "#F3ECDD"),
    "teal": ("#0F3B3A", "#1C5A57", "#9ED3CD", "#F3ECDD"),
    "brick": ("#7A2E25", "#B5452F", "#F4D6A0", "#FFF4E0"),
    "navy": ("#1E2F4F", "#2F4A7A", "#F28C6B", "#F3ECDD"),
    "paper": ("#EADBB8", "#D9C394", "#B5452F", "#2A1E18"),
    "noir": ("#141414", "#2A2525", "#E8463A", "#F3ECDD"),
    "moss": ("#3B5D3A", "#557A4E", "#F2E3B3", "#F8F1DC"),
    "gold": ("#F2C14E", "#E3A23A", "#1E2F4F", "#172131"),
    "rose": ("#5B2A44", "#8C3B5E", "#F7B2A8", "#FFEDE8"),
    "slate": ("#20384A", "#2E5470", "#F2E8CF", "#F2E8CF"),
    "mint": ("#C9D9D3", "#A8C3BA", "#1F4D4A", "#14302E"),
    "rust": ("#3A1F14", "#6B3A22", "#F2A65A", "#FBE7CF"),
    "dusk": ("#3D2352", "#B8494A", "#F6B352", "#FFF1DE"),
    "blood": ("#160C0E", "#33141A", "#D7263D", "#F3ECDD"),
    "frost": ("#DCE6EE", "#B6CADA", "#FFFFFF", "#1E2F4F"),
    "sand": ("#EBCB91", "#D9A55B", "#8A3B12", "#2A1E18"),
    "ocean": ("#0B2E4F", "#145A80", "#7FD1C7", "#EAF6F3"),
    "pine": ("#16302A", "#24493D", "#C7D9A8", "#F0F4E6"),
    "blush": ("#F4C7C3", "#E8A3A0", "#5B2A44", "#3A1A2A"),
    "ink": ("#0E1726", "#1B2A44", "#F3ECDD", "#F3ECDD"),
}
ALL_PALETTES = list(PALETTES)


# --- shape helpers. A shape is (kind, data, colour, alpha); colour is "c" (motif), "i" (text colour),
# "k" (black), "t" (the poster's top colour) or a hex string.
def _P(pts, col="c", a=1.0):
    return ("poly", [(round(x, 2), round(y, 2)) for x, y in pts], col, a)


def _C(cx, cy, r, col="c", a=1.0):
    return ("ell", (cx, cy, r, r), col, a)


def _E(cx, cy, rx, ry, col="c", a=1.0):
    return ("ell", (cx, cy, rx, ry), col, a)


def _R(x, y, w, h, col="c", a=1.0):
    return _P([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], col, a)


def _O(cx, cy, rx, ry, sw, col="c", a=1.0):
    return ("ring", (cx, cy, rx, ry, sw), col, a)


def _rot(pts, cx, cy, deg):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def _bar(x1, y1, x2, y2, w, col="c", a=1.0):
    dx, dy = x2 - x1, y2 - y1
    n = math.hypot(dx, dy) or 1
    ox, oy = -dy / n * w / 2, dx / n * w / 2
    return _P([(x1 + ox, y1 + oy), (x2 + ox, y2 + oy), (x2 - ox, y2 - oy), (x1 - ox, y1 - oy)], col, a)


def _star(cx, cy, s, col="i", a=.9):
    q = s * .22
    return _P([(cx, cy - s), (cx + q, cy - q), (cx + s, cy), (cx + q, cy + q),
               (cx, cy + s), (cx - q, cy + q), (cx - s, cy), (cx - q, cy - q)], col, a)


def _crescent(cx, cy, r, d, tilt=-25, col="c", a=1.0):
    h = math.sqrt(max(r * r - (d / 2) ** 2, 0))
    tT, tB = math.atan2(-h, d / 2), math.atan2(h, d / 2)
    pts = [(cx + r * math.cos(t), cy + r * math.sin(t))
           for t in (tT + (tB - 2 * math.pi - tT) * i / 40 for i in range(41))]
    fB, fT = math.atan2(h, -d / 2), math.atan2(-h, -d / 2) + 2 * math.pi
    pts += [(cx + d + r * math.cos(f), cy + r * math.sin(f)) for f in (fB + (fT - fB) * i / 40 for i in range(41))]
    return _P(_rot(pts, cx, cy, tilt), col, a)


def _heart(cx, cy, s, col="c", a=1.0):
    pts = []
    for i in range(60):
        t = 2 * math.pi * i / 60
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * s / 16, cy + y * s / 16))
    return _P(pts, col, a)


def _wave(y0, amp, length, phase=0.0, col="c", a=1.0):
    pts = [(x, y0 + amp * math.sin(2 * math.pi * (x / length) + phase)) for x in range(-4, 106, 3)]
    return _P(pts + [(105, 151), (-5, 151)], col, a)


def _flame(cx, base, w, h, sway=0.0, col="c", a=1.0):
    left, right = [], []
    for i in range(25):
        f = i / 24
        hw = w * math.sin(math.pi * f ** 0.6) * (1 - f * .15)
        x = cx + sway * f * f
        y = base - h * f
        left.append((x - hw, y))
        right.append((x + hw, y))
    return _P(left + right[::-1], col, a)


def _rand(seed, n):
    """n deterministic numbers in [0, 1) from a seed."""
    out, x = [], seed or 1
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(x / 0x7FFFFFFF)
    return out


# --- themed designs: each takes a seed and returns a list of shapes
def m_sunrise(s):
    out = [_C(50, 112, 30), _R(0, 112, 100, 38, "k", .28)]
    for y in (97, 103, 108):  # slats across the sun only
        hw = math.sqrt(30 ** 2 - (112 - y) ** 2)
        out.append(_R(50 - hw, y, hw * 2, 1.6, "t", .6))
    return out


def m_sunset(s):
    out = [_C(50, 106, 26)]
    out += [_R(0, 106 + i * 7, 100, 3.4 - i * .4, "c", .85 - i * .15) for i in range(5)]
    return out


def m_moon(s):
    r = _rand(s, 12)
    out = [_crescent(62, 96, 20, 12)]
    out += [_star(10 + r[i] * 80, 72 + r[i + 6] * 70, 1.6 + r[i] * 1.8) for i in range(6)
            if not (44 < 10 + r[i] * 80 < 84 and 72 < 72 + r[i + 6] * 70 < 120)]
    return out


def _band(cx, cy, rx, ry, sw, t1, t2, col="c", a=1.0):
    ts = [t1 + (t2 - t1) * i / 30 for i in range(31)]
    outer = [(cx + (rx + sw / 2) * math.cos(t), cy + (ry + sw / 2) * math.sin(t)) for t in ts]
    inner = [(cx + (rx - sw / 2) * math.cos(t), cy + (ry - sw / 2) * math.sin(t)) for t in ts[::-1]]
    return _P(outer + inner, col, a)


def m_space(s):
    r = _rand(s, 10)
    out = [_star(8 + r[i] * 84, 66 + r[i + 5] * 80, 1.2 + r[i] * 1.6) for i in range(5)]
    return out + [_band(52, 104, 33, 7, 2.4, math.pi, 2 * math.pi, "i", .85), _C(52, 104, 18),
                  _band(52, 104, 33, 7, 2.4, 0, math.pi, "i", .85)]


def m_sea(s):
    return [_wave(100, 3.5, 34, s % 7, "c", .45), _wave(113, 3.5, 30, 1 + s % 5, "c", .7),
            _wave(127, 3.5, 26, 2 + s % 3, "c", 1.0)]


def m_fin(s):
    return [_wave(108, 2.5, 30, 0, "c", .55), _P([(44, 116), (60, 84), (66, 116)], "k", .55),
            _wave(116, 3, 26, 1.3, "c", 1.0)]


def m_fire(s):
    return [_flame(30, 150, 13, 52, 4, "c", .7), _flame(70, 150, 13, 48, -5, "c", .7),
            _flame(50, 152, 17, 70, 3, "c", 1.0), _flame(50, 150, 7, 34, -2, "t", .75)]


def m_heart(s):
    return [_heart(64, 98, 15, "c", .55), _heart(44, 108, 22)]


def m_city(s):
    r = _rand(s, 30)
    out, x = [], -2
    i = 0
    while x < 100:
        w = 10 + r[i] * 10
        h = 30 + r[i + 1] * 42
        out.append(_R(x, 150 - h, w, h, "c", .55 + (i % 2) * .45))
        if (i % 2):
            for wy in range(int(150 - h + 5), 146, 7):
                for wx in (x + 2.5, x + w - 5):
                    if r[(int(wy) + int(wx)) % 30] > .45:
                        out.append(_R(wx, wy, 2.4, 3, "t", .65))
        x += w + .5
        i += 2
    return out


def m_road(s):
    out = [_P([(0, 92), (100, 92), (100, 150), (0, 150)], "k", .22),
           _P([(47, 92), (53, 92), (96, 150), (4, 150)], "k", .35),
           _C(50, 86, 9, "c", .9)]
    for i, (y1, y2) in enumerate([(96, 100), (106, 113), (121, 132), (140, 150)]):
        w1, w2 = .5 + i * .6, .9 + i * .9
        out.append(_P([(50 - w1, y1), (50 + w1, y1), (50 + w2, y2), (50 - w2, y2)], "c"))
    return out


def m_house(s):
    return [_R(0, 134, 100, 16, "k", .25), _P([(24, 104), (50, 80), (76, 104)]), _R(29, 103, 42, 32),
            _R(36, 110, 10, 10, "i", .9), _R(55, 116, 9, 19, "t", .9)]


def m_windows(s):
    r = _rand(s, 12)
    out = []
    for row in range(3):
        for col in range(3):
            lit = r[row * 3 + col] > .45
            out.append(_R(16 + col * 24, 72 + row * 24, 18, 19, "c", 1 if lit else .22))
    return out


def m_mountain(s):
    return [_C(74, 84, 9, "c", .6), _P([(-12, 150), (28, 92), (68, 150)], "c", .55),
            _P([(22, 150), (62, 76), (108, 150)]), _P([(62, 76), (55.4, 88), (68.8, 88)], "i", .9)]


def m_eye(s):
    top = [(x, 108 - 17 * math.sin(math.pi * (x - 14) / 72)) for x in range(14, 87, 2)]
    bot = [(x, 108 + 13 * math.sin(math.pi * (x - 14) / 72)) for x in range(86, 13, -2)]
    return [_P(top + bot), _C(50, 107, 11.5, "t"), _C(50, 107, 5, "c")]


def m_birds(s):
    r = _rand(s, 14)
    out = []
    for i in range(6):
        x, y, z = 14 + r[i] * 72, 74 + r[i + 6] * 62, 5 + r[i] * 5
        out.append(_P([(x - z, y - z * .4), (x, y), (x + z, y - z * .4), (x + z, y - z * .15),
                       (x, y + z * .3), (x - z, y - z * .15)], "c", .65 + r[i + 3] * .35))
    return out


def m_forest(s):
    out = []
    for x, base, h, a in ((20, 150, 52, .55), (78, 150, 58, .55), (36, 150, 66, 1), (60, 150, 74, 1)):
        for j in range(3):
            w = h * (.36 - j * .08)
            y = base - 8 - j * h * .27
            out.append(_P([(x - w, y), (x, y - h * .42), (x + w, y)], "c", a))
        out.append(_R(x - 2, base - 9, 4, 9, "c", a))
    return out


def m_flower(s):
    cx, cy = 50, 100
    out = [_bar(50, 112, 50, 152, 2.4, "c", .8)]
    out += [_C(cx + 11 * math.cos(math.radians(a)), cy + 11 * math.sin(math.radians(a)), 8, "c", .9)
            for a in range(0, 360, 60)]
    return out + [_C(cx, cy, 6.5, "i", .95)]


def _flake(cx, cy, r):
    out = []
    for a in range(0, 180, 60):
        t = math.radians(a)
        out.append(_bar(cx - r * math.cos(t), cy - r * math.sin(t), cx + r * math.cos(t), cy + r * math.sin(t), 1.3))
    return out


def m_snow(s):
    r = _rand(s, 12)
    out = [_E(50, 164, 82, 30, "c", .95)]
    for i in range(6):
        out += _flake(12 + r[i] * 76, 70 + r[i + 6] * 56, 3.5 + r[i] * 4.5)
    return out


def m_storm(s):
    r = _rand(s, 30)
    out = [_bar(x, y, x - 6, y + 14, 1, "c", .45) for x, y in ((8 + r[i] * 90, 66 + r[i + 15] * 74) for i in range(14))]
    return out + [_P([(58, 70), (38, 108), (50, 108), (40, 146), (66, 98), (53, 98), (64, 70)])]


def m_drips(s):
    r = _rand(s, 12)
    out = [_R(0, 64, 100, 7)]
    for i in range(7):
        x = 6 + i * 14.5 + r[i] * 4
        ln = 10 + r[i + 5] * 50
        out += [_R(x - 2, 70, 4, ln), _C(x, 70 + ln, 2.6)]
    return out


def m_crosshair(s):
    return [_O(50, 104, 24, 24, 2.4), _O(50, 104, 10, 10, 1.6, "c", .7),
            _R(49, 72, 2, 18), _R(49, 118, 2, 18), _R(18, 103, 18, 2), _R(64, 103, 18, 2), _C(50, 104, 2.2)]


def m_ghost(s):
    pts = [(50 + 20 * math.cos(math.radians(a)), 96 + 20 * math.sin(math.radians(a))) for a in range(180, 361, 10)]
    pts += [(70, 136)] + [(70 - i * 40 / 8, 136 + (4 if i % 2 else -1)) for i in range(1, 9)] + [(30, 96)]
    return [_P(pts, "c", .92), _E(43, 98, 3, 4.2, "t"), _E(57, 98, 3, 4.2, "t"), _E(50, 111, 3.6, 4.6, "t", .8)]


def m_crown(s):
    return [_P([(24, 124), (24, 92), (37, 106), (50, 84), (63, 106), (76, 92), (76, 124)]),
            _C(24, 90, 3.4), _C(50, 82, 3.4), _C(76, 90, 3.4), _R(24, 128, 52, 6), _C(50, 113, 3.6, "t", .9)]


def m_tracks(s):
    out = [_P([(48.6, 82), (49.4, 82), (24, 150), (18, 150)]), _P([(50.6, 82), (51.4, 82), (82, 150), (76, 150)])]
    for i in range(7):
        f = (i / 6) ** 1.7
        y = 88 + 62 * f
        hw = 3 + 34 * f
        out.append(_R(50 - hw, y, hw * 2, .8 + 2.6 * f, "c", .7))
    return out


def m_record(s):
    out = [_C(50, 106, 32, "k", .8)]
    out += [_O(50, 106, rr, rr, .5, "c", .3) for rr in (28, 24, 20, 16)]
    return out + [_C(50, 106, 10), _C(50, 106, 1.6, "t")]


def m_clock(s):
    out = [_O(50, 104, 25, 25, 3), _C(50, 104, 2.6)]
    out += [_bar(50 + 20 * math.cos(math.radians(a)), 104 + 20 * math.sin(math.radians(a)),
                 50 + 23 * math.cos(math.radians(a)), 104 + 23 * math.sin(math.radians(a)), 2) for a in range(0, 360, 30)]
    return out + [_bar(50, 104, 50, 88, 2.4), _bar(50, 104, 61, 110, 2.4)]


def _cloud(cx, cy, s, a):
    return [_C(cx - s * .9, cy, s * .7, "c", a), _C(cx, cy - s * .35, s, "c", a), _C(cx + s * .95, cy + s * .05, s * .65, "c", a),
            _R(cx - s * .9, cy, s * 1.85, s * .7, "c", a)]


def m_clouds(s):
    return _cloud(34, 92, 11, .6) + _cloud(62, 118, 15, 1)


def m_dunes(s):
    return [_C(72, 90, 10, "c", .9), _wave(112, 8, 120, 0.4, "c", .55), _wave(128, 7, 90, 2.2, "c", 1.0)]


def m_keyhole(s):
    return [_C(50, 98, 11), _P([(45, 102), (55, 102), (60, 134), (40, 134)])]


def m_balloon(s):
    out = []
    for cx, cy, r, a in ((34, 100, 11, .7), (62, 92, 13, 1), (50, 112, 9, .55)):
        out += [_E(cx, cy, r, r * 1.2, "c", a), _bar(cx, cy + r * 1.2, cx + 2, 150, .6, "i", .6)]
    return out


def m_confetti(s):
    r = _rand(s, 60)
    out = []
    for i in range(16):
        x, y = 6 + r[i] * 88, 66 + r[i + 16] * 80
        col = "c" if i % 3 else "i"
        if i % 2:
            out.append(_C(x, y, 1.6 + r[i + 32] * 1.6, col, .9))
        else:
            out.append(_P(_rot([(x - 3, y - 1.2), (x + 3, y - 1.2), (x + 3, y + 1.2), (x - 3, y + 1.2)], x, y, r[i + 40] * 180), col, .9))
    return out


def m_sunburst(s):
    out = []
    for i in range(12):
        a1, a2 = math.radians(180 + i * 15), math.radians(180 + i * 15 + 7.5)
        out.append(_P([(50, 150), (50 + 140 * math.cos(a1), 150 + 140 * math.sin(a1)),
                       (50 + 140 * math.cos(a2), 150 + 140 * math.sin(a2))], "c", .32))
    return out + [_C(50, 150, 16)]


def m_swords(s):  # war and battle: crossed swords
    return [_bar(26, 136, 72, 80, 3.2), _bar(74, 136, 28, 80, 3.2, "c", .75),
            _bar(30, 120, 40, 130, 2.2), _bar(70, 120, 60, 130, 2.2, "c", .75)]


def m_columns(s):
    out = [_P([(16, 84), (50, 70), (84, 84)]), _R(16, 86, 68, 4), _R(14, 136, 72, 5)]
    return out + [_R(x, 92, 6, 42, "c", .85) for x in (22, 37, 57, 72)]


def m_spiral(s):  # dreams, vertigo
    pts = []
    for i in range(140):
        t = i / 140 * 6 * math.pi
        rr = 1 + t * 1.45
        pts.append((50 + rr * math.cos(t), 104 + rr * math.sin(t)))
    return [_bar(*pts[i], *pts[i + 1], 2.2) for i in range(len(pts) - 1)]


def m_bars(s):  # prison, cage
    return [_R(0, 66, 100, 4)] + [_R(x, 66, 3.5, 84) for x in range(10, 100, 16)]


# abstract designs (no theme)
def a_disc(s): return [_C(50, 96, 21)]
def a_horizon(s): return [_C(50, 105, 25), _R(0, 105, 100, 45, "k", .2)]
def a_bands(s): return [_R(0, y, 100, 9) for y in (81, 99, 117)]
def a_slope(s): return [_P([(100, 45), (100, 150), (0, 150), (0, 138)], "c", .85)]
def a_rings(s): return [_O(50, 99, rr, rr, 4) for rr in (6, 15, 25, 35)] + [_C(50, 99, 5)]
def a_dome(s): return [_E(50, 150, 46, 54)]
def a_beam(s): return [_P([(50, 0), (5, 150), (62, 150)], "c", .43)]
def a_pair(s): return [_C(37, 96, 21, "c", .82), _C(63, 96, 21, "c", .5)]
def a_gate(s): return [_R(30, 82, 6, 47), _R(64, 82, 6, 47)]
def a_stripes(s): return [_bar(x, 160, x + 40, 60, 5, "c", .8) for x in range(-40, 100, 16)]
def a_dots(s): return [_C(16 + c * 17, 80 + r * 17, 3.6, "c", .9) for r in range(4) for c in range(5)]
def a_peak(s): return [_P([(10, 140), (50, 72), (90, 140)])]
def a_arch(s): return [_R(30, 100, 40, 50), _C(50, 100, 20)]
def a_squares(s): return [_O(50, 108, z, z, 3) for z in (8, 18, 28)]
def a_zigzag(s): return [_bar(x, 112 + (8 if i % 2 else -8), x + 12.5, 112 + (-8 if i % 2 else 8), 4) for i, x in enumerate(range(-5, 105, 12))]
def a_scallops(s): return [_C(x, 126, 9) for x in range(0, 110, 18)] + [_R(0, 126, 100, 24)]
def a_offset(s): return [_C(84, 120, 38, "c", .85), _C(22, 92, 9, "i", .7)]
def a_split(s): return [_P([(0, 150), (100, 70), (100, 150)], "c", .45), _C(40, 104, 16)]


def a_checker(s):
    return [_R(c * 12.5, 110 + r * 12.5, 12.5, 12.5) for r in range(3) for c in range(8) if (r + c) % 2]


ABSTRACT = [a_disc, a_horizon, a_bands, a_slope, a_rings, a_dome, a_beam, a_pair, a_gate, a_stripes, a_dots,
            a_peak, a_arch, a_squares, a_zigzag, a_scallops, a_offset, a_split, a_checker, m_sunburst]

# title words -> (design, palettes that suit it)
THEMES = [
    (("sunrise", "dawn", "morning"), m_sunrise, ("dusk", "gold", "blush", "plum")),
    (("sunset", "dusk", "evening", "summer", "sun", "sunshine", "day", "days"), m_sunset, ("dusk", "rust", "brick", "plum", "gold")),
    (("moon", "moonlight", "moonrise", "night", "nights", "midnight", "dark", "darkness", "nocturnal", "sleep", "sleepless"),
     m_moon, ("ink", "navy", "plum", "slate")),
    (("star", "stars", "space", "planet", "galaxy", "interstellar", "mars", "alien", "aliens", "solaris", "gravity",
      "moonfall", "orbit", "cosmos", "universe", "apollo", "astronaut", "odyssey", "arrival", "martian"),
     m_space, ("ink", "navy", "plum", "noir")),
    (("jaws", "shark", "sharks"), m_fin, ("ocean", "teal", "navy")),
    (("sea", "ocean", "water", "waters", "river", "lake", "island", "beach", "wave", "waves", "boat", "ship", "titanic",
      "sail", "lighthouse", "surf", "tide", "deep", "swim", "swimming", "pool", "flood", "harbour", "bay"),
     m_sea, ("ocean", "teal", "slate", "navy")),
    (("fire", "fires", "burn", "burning", "flame", "flames", "heat", "hell", "inferno", "blaze", "smoke", "hot", "volcano"),
     m_fire, ("noir", "brick", "blood", "rust")),
    (("love", "lovers", "heart", "hearts", "kiss", "romance", "valentine", "darling", "sweetheart", "marriage", "wedding",
      "bride", "honey", "crush", "amour", "amelie", "her", "notebook"),
     m_heart, ("rose", "blush", "brick", "plum")),
    (("city", "new york", "manhattan", "paris", "london", "tokyo", "rome", "berlin", "chicago", "los angeles",
      "street", "streets", "metropolis", "downtown", "town", "brooklyn", "hong kong", "vegas", "detroit", "gotham",
      "skyfall", "broadway"),
     m_city, ("navy", "ink", "slate", "noir", "dusk")),
    (("road", "drive", "driver", "highway", "car", "cars", "taxi", "ride", "journey", "trip", "route", "speed",
      "rush", "miles", "travel", "travels", "easy rider", "thelma"),
     m_road, ("dusk", "sand", "rust", "gold")),
    (("train", "trains", "express", "station", "railway", "snowpiercer", "locomotive"), m_tracks, ("rust", "slate", "noir", "paper")),
    (("house", "home", "hotel", "mansion", "cabin", "cottage", "castle", "manor", "motel", "lodge"),
     m_house, ("navy", "moss", "plum", "slate")),
    (("apartment", "window", "windows", "room", "rooms", "rear window", "neighbours", "neighbors", "building", "tower"),
     m_windows, ("navy", "ink", "noir", "slate")),
    (("mountain", "mountains", "hill", "hills", "peak", "valley", "everest", "summit", "climb", "alps", "brokeback", "cliff"),
     m_mountain, ("slate", "navy", "moss", "pine")),
    (("eye", "eyes", "see", "seeing", "look", "looking", "watch", "watcher", "watching", "vision", "witness", "blind",
      "spy", "gaze", "stare", "peeping", "seen", "observer", "surveillance", "vertigo"),
     m_eye, ("noir", "plum", "teal", "gold")),
    (("bird", "birds", "wings", "fly", "flying", "flight", "eagle", "crow", "raven", "hawk", "dove", "swan", "sparrow",
      "mockingbird", "birdman", "cuckoo", "goose", "heron"),
     m_birds, ("frost", "paper", "slate", "dusk")),
    (("tree", "trees", "forest", "woods", "wood", "jungle", "wild", "wilderness", "grove", "pine", "garden", "princess mononoke"),
     m_forest, ("pine", "moss", "mint", "teal")),
    (("flower", "flowers", "rose", "roses", "lily", "blossom", "bloom", "petal", "daisy", "orchid", "lotus", "tulip", "midsommar"),
     m_flower, ("blush", "rose", "moss", "mint")),
    (("snow", "winter", "frozen", "ice", "cold", "christmas", "xmas", "frost", "blizzard", "fargo", "arctic", "polar", "thing"),
     m_snow, ("frost", "slate", "ink")),
    (("rain", "storm", "storms", "thunder", "lightning", "tornado", "twister", "hurricane", "tempest", "weather"),
     m_storm, ("slate", "ink", "navy", "noir")),
    (("blood", "bloody", "scream", "massacre", "chainsaw", "slasher", "carrie", "evil", "devil", "hereditary", "saw",
      "terror", "horror", "zombie", "zombies", "undead", "vampire", "dracula"),
     m_drips, ("blood", "noir", "paper")),
    (("kill", "killer", "killers", "killing", "murder", "murders", "gun", "guns", "shot", "shoot", "shooter", "hitman",
      "assassin", "bullet", "sniper", "target", "heist", "gangster", "godfather", "goodfellas", "mafia", "crime", "wick"),
     m_crosshair, ("noir", "blood", "slate", "paper")),
    (("ghost", "ghosts", "haunt", "haunted", "haunting", "spirit", "spirited", "spirits", "phantom", "poltergeist",
      "shining", "conjuring", "others", "boo", "spectre"),
     m_ghost, ("ink", "plum", "teal", "slate")),
    (("king", "kings", "queen", "queens", "prince", "princess", "crown", "royal", "empire", "kingdom", "emperor",
      "throne", "lord", "duchess", "favourite", "favorite", "monarch"),
     m_crown, ("plum", "rose", "noir", "gold")),
    (("music", "song", "songs", "sing", "singing", "dance", "dancing", "jazz", "rock", "band", "melody", "musical",
      "record", "whiplash", "la la land", "disco", "opera", "concert", "piano", "drum", "beat", "soul"),
     m_record, ("gold", "dusk", "plum", "rose")),
    (("time", "clock", "hour", "hours", "minute", "minutes", "tomorrow", "yesterday", "today", "forever", "past",
      "future", "back to the future", "memento", "tenet", "looper", "groundhog", "noon"),
     m_clock, ("paper", "slate", "mint", "gold")),
    (("cloud", "clouds", "sky", "skies", "heaven", "heavens", "air", "angel", "angels", "wind", "breath"),
     m_clouds, ("mint", "frost", "navy", "dusk")),
    (("dream", "dreams", "dreaming", "dreamer", "inception", "hypnosis", "trance", "mind", "madness", "insomnia",
      "spiral", "twisted"),
     m_spiral, ("plum", "teal", "noir", "dusk")),
    (("desert", "dune", "dunes", "sand", "arabia", "sahara", "mirage", "oasis", "western", "outlaw", "cowboy", "cowboys",
      "west", "texas", "mexico", "arizona"),
     m_dunes, ("sand", "rust", "dusk", "gold")),
    (("secret", "secrets", "mystery", "key", "keys", "door", "doors", "lock", "locked", "hidden", "knives", "clue",
      "riddle", "puzzle", "enigma", "zodiac", "gone", "prisoners"),
     m_keyhole, ("noir", "slate", "paper", "plum")),
    (("prison", "jail", "cage", "escape", "shawshank", "alcatraz", "captive", "trapped"), m_bars, ("slate", "noir", "paper")),
    (("war", "battle", "battles", "soldier", "soldiers", "sword", "swords", "army", "warrior", "warriors", "samurai",
      "knight", "knights", "gladiator", "dunkirk", "1917", "troy", "spartacus"),
     m_swords, ("rust", "noir", "slate", "paper")),
    (("party", "birthday", "celebration", "fun", "funny", "comedy", "laugh", "carnival", "circus", "festival"),
     m_confetti, ("gold", "rose", "teal", "blush")),
    (("balloon", "balloons", "up", "paddington", "toy", "toys", "kid", "kids", "child", "children", "family"),
     m_balloon, ("rose", "mint", "gold", "navy")),
    (("rome", "athens", "greek", "god", "gods", "temple", "ancient", "history", "senate", "republic", "olympus"),
     m_columns, ("paper", "sand", "slate", "rust")),
]
_THEME_WORDS = {}
for _words, _fn, _pals in THEMES:
    for _w in _words:
        _THEME_WORDS.setdefault(_w, (_fn, _pals))

GENRE_THEMES = {
    "Horror": [(m_drips, ("blood", "noir")), (m_ghost, ("ink", "plum")), (m_eye, ("noir", "blood"))],
    "Science Fiction": [(m_space, ("ink", "navy", "plum"))],
    "Romance": [(m_heart, ("rose", "blush", "plum"))],
    "Crime": [(m_crosshair, ("noir", "slate", "paper")), (m_windows, ("noir", "ink"))],
    "Thriller": [(m_eye, ("noir", "slate", "plum")), (m_keyhole, ("noir", "slate"))],
    "Mystery": [(m_keyhole, ("noir", "slate", "plum"))],
    "Western": [(m_dunes, ("sand", "rust", "dusk"))],
    "War": [(m_swords, ("rust", "slate", "noir"))],
    "Music": [(m_record, ("gold", "dusk", "plum"))],
    "Animation": [(m_clouds, ("mint", "frost", "dusk")), (m_balloon, ("rose", "mint", "gold"))],
    "Family": [(m_balloon, ("rose", "mint", "gold")), (m_clouds, ("mint", "frost"))],
    "Fantasy": [(m_moon, ("plum", "ink", "navy")), (m_crown, ("plum", "rose"))],
    "Adventure": [(m_mountain, ("slate", "pine", "moss")), (m_road, ("dusk", "sand"))],
    "History": [(m_columns, ("paper", "sand", "slate")), (m_crown, ("plum", "noir"))],
    "Comedy": [(m_confetti, ("gold", "rose", "teal", "blush"))],
}
GENRE_ORDER = ["Horror", "Science Fiction", "Western", "War", "Music", "Animation", "Romance", "Crime", "Mystery",
               "Thriller", "Fantasy", "History", "Family", "Adventure", "Comedy"]


def _theme_for(name):
    words = title_key(name).split()
    text = " " + " ".join(words) + " "
    for phrase, hit in _THEME_WORDS.items():  # multi-word phrases first
        if " " in phrase and f" {phrase} " in text:
            return hit
    for w in words:
        if w in _THEME_WORDS:
            return _THEME_WORDS[w]
    for w in words:  # compound words: "aftersun", "moonstruck", "nightcrawler"
        for stem in ("sunrise", "sunset", "sun", "moon", "night", "star", "fire", "snow", "rain", "blood", "heart", "love"):
            if len(w) > len(stem) + 2 and (w.startswith(stem) or w.endswith(stem)):
                if stem == "rain" and "train" in w:
                    continue
                return _THEME_WORDS[stem]
    return None


def poster_design(k, name, genres=()):
    """Pick the design and palette for a film: title first, then genre, then a hash."""
    h = zlib.crc32(str(k).encode())
    hit = _theme_for(name)
    if not hit:
        for g in GENRE_ORDER:
            if g in (genres or ()):
                options = GENRE_THEMES[g]
                hit = options[h % len(options)]
                break
    if hit:
        fn, pals = hit
        pal = pals[(h // 7) % len(pals)]
    else:
        fn = ABSTRACT[(h // 13) % len(ABSTRACT)]
        pal = ALL_PALETTES[h % len(ALL_PALETTES)]
    return fn, pal, h


def _shape_colour(col, palette):
    top, bottom, c, ink = PALETTES[palette]
    return {"c": c, "i": ink, "k": "#000000", "t": top}.get(col, col)


@functools.lru_cache(maxsize=4096)
def poster_svg(k, name, genres=(), avoid_gold=False):
    """(css background, text colour) for the generated poster."""
    fn, pal, h = poster_design(k, name, genres)
    if avoid_gold and pal == "gold":
        pal = "dusk"
    top, bottom, _, ink = PALETTES[pal]
    parts = []
    for kind, data, col, a in fn(h):
        fill = _shape_colour(col, pal)
        op = f' fill-opacity="{a:g}"' if a < 1 else ""
        if kind == "poly":
            pts = " ".join(f"{x:g},{y:g}" for x, y in data)
            parts.append(f"<polygon points='{pts}' fill='{fill}'{op}/>")
        elif kind == "ell":
            cx, cy, rx, ry = data
            parts.append(f"<ellipse cx='{cx:g}' cy='{cy:g}' rx='{rx:g}' ry='{ry:g}' fill='{fill}'{op}/>")
        else:
            cx, cy, rx, ry, sw = data
            sop = f' stroke-opacity="{a:g}"' if a < 1 else ""
            parts.append(f"<ellipse cx='{cx:g}' cy='{cy:g}' rx='{rx:g}' ry='{ry:g}' fill='none' stroke='{fill}' "
                         f"stroke-width='{sw:g}'{sop}/>")
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 150' preserveAspectRatio='xMidYMax slice'>"
           + "".join(parts) + "</svg>")
    uri = "data:image/svg+xml," + urllib.parse.quote(svg, safe="=:/,.-")
    return f"url({uri}) center bottom / 100% 100% no-repeat, linear-gradient(170deg,{top},{bottom})", ink


def draw_poster_art(img, k, name, genres=()):
    """Draw the same design onto a Pillow image (share images)."""
    fn, pal, h = poster_design(k, name, genres)
    w, ht = img.size
    S = 2  # draw at double size, then shrink, for smooth edges
    sx, sy = w * S / 100, ht * S / 150
    layer = Image.new("RGBA", (w * S, ht * S), (0, 0, 0, 0))
    for kind, data, col, a in fn(h):
        shape = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(shape)
        rgb = _hex(_shape_colour(col, pal))
        fill = rgb + (int(255 * a),)
        if kind == "poly":
            d.polygon([(x * sx, y * sy) for x, y in data], fill=fill)
        elif kind == "ell":
            cx, cy, rx, ry = data
            d.ellipse([(cx - rx) * sx, (cy - ry) * sy, (cx + rx) * sx, (cy + ry) * sy], fill=fill)
        else:
            cx, cy, rx, ry, sw = data
            d.ellipse([(cx - rx - sw / 2) * sx, (cy - ry - sw / 2) * sy, (cx + rx + sw / 2) * sx, (cy + ry + sw / 2) * sy],
                      outline=fill, width=max(1, int(sw * sx)))
        layer = Image.alpha_composite(layer, shape)
    layer = layer.resize((w, ht), Image.LANCZOS)
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB"), PALETTES[pal]


def poster(k, size="md", link=True) -> str:
    r = cat.loc[k]
    return poster_html(k, r["Name"], year_str(r["Year"]), r["Letterboxd URI"], size, link)


def _known_genres(name, year, k=None) -> tuple:
    """Genres if this film's details are already loaded (never fetches)."""
    if not year and k and "|" in str(k):  # swipe cards hide the year, but the key still has it
        year = str(k).rsplit("|", 1)[1]
    try:
        hit = _film_store()["films"].get(_detail_key(name, _norm_year(year)))
    except Exception:
        return ()
    return tuple((hit[1] or {}).get("genres") or ()) if hit else ()


def poster_html(k, name, year, uri, size="md", link=True) -> str:
    """A screen-printed 'poster' generated from the film's title (and genre), so every film has art."""
    bg, ink = poster_svg(str(k), str(name), _known_genres(name, year, k), avoid_gold=(size == "lg"))
    style = f"--pi:{ink};background:{bg}"
    inner = f'<span class="p-t">{esc(str(name))}</span><span class="p-y">{esc(str(year or ""))}</span>'
    real = real_poster_url(k, size)
    if real:  # the film's actual poster on top; the titled art stays underneath if it's slow or fails
        size += " real"
        # a background image, so a poster that fails to load just leaves the art showing (no broken-image icon)
        inner += f'<span class="p-img" style="background-image:url(&quot;{esc(real)}&quot;)"></span>'
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

NUM_WORDS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def ticket(k, name, year, uri, why_html: str, kicker: str = "Tonight's feature", flip: str = "a", admit: int = 2) -> str:
    link = (f'<a class="tk-link" href="{esc(str(uri))}" target="_blank">Open on Letterboxd →</a>'
            if uri and pd.notna(uri) else "")
    serial = f"No. {zlib.crc32(str(k).encode()) % 900000 + 100000}"
    return (f'<div class="ticket {flip}"><div class="tk-stub"><span>Admit {NUM_WORDS[admit] if 0 < admit < len(NUM_WORDS) else admit}</span><small>{serial}</small></div>'
            f'<div class="tk-main"><div class="tk-kick">{kicker}</div>'
            f'<div class="tk-body">{poster_html(k, name, year, uri, "lg", link=False)}<div style="min-width:0">'
            f'<div class="tk-title">{esc(str(name))}</div><div class="tk-year">{esc(str(year or ""))}</div>'
            f'<div class="tk-why">{why_html}</div></div></div>{link}</div></div>')


# ---------- Film details and streaming (optional, from TMDB) ----------
# Runtime, genres and where each film is streaming. Needs a free TMDB key in Streamlit secrets
# (TMDB_API_KEY). Without one, everything else works and these extras simply don't appear.

TMDB_API = "https://api.themoviedb.org/3"
DETAIL_TTL = 7 * 24 * 3600
FAKE_TMDB = os.environ.get("DOUBLE_FEATURE_FAKE_TMDB") == "1"  # offline testing only
REGIONS = {"GB": "UK", "IE": "Ireland", "US": "USA", "CA": "Canada", "AU": "Australia", "NZ": "New Zealand",
           "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy", "NL": "Netherlands", "BE": "Belgium",
           "SE": "Sweden", "DK": "Denmark", "NO": "Norway", "PT": "Portugal", "IN": "India", "JP": "Japan",
           "SG": "Singapore", "ZA": "South Africa"}


def _secret(name):
    try:
        val = st.secrets.get(name)
    except Exception:  # no secrets file at all
        val = None
    return val or os.environ.get(name)


def tmdb_on() -> bool:
    return FAKE_TMDB or bool(_secret("TMDB_API_KEY"))


def current_region() -> str:
    if st.session_state.get("region") in REGIONS:
        return st.session_state.region
    loc = (st.context.locale or "") if hasattr(st.context, "locale") else ""
    cc = loc.replace("_", "-").split("-")[-1].upper() if "-" in loc.replace("_", "-") else ""
    return cc if cc in REGIONS else "GB"


POSTER_URLS = {}  # film key -> TMDB poster path, filled as film details arrive this run
POSTER_WIDTHS = {"sm": "w154", "md": "w342", "lg": "w342", "xl": "w500"}


def want_real_posters() -> bool:
    return tmdb_on() and st.session_state.get("poster_style") == "real"


def remember_posters(infos):
    for k, i in (infos or {}).items():
        if i and i.get("poster"):
            POSTER_URLS[k] = i["poster"]


def real_poster_url(k, size="md"):
    path = POSTER_URLS.get(k) if st.session_state.get("poster_style") == "real" else None
    if not path:
        return ""
    if FAKE_TMDB:  # offline testing: a plain coloured stand-in
        col = ["#8a3b2e", "#2e5a8a", "#3b8a4e", "#6b3b8a", "#8a7a2e", "#2e7a8a", "#444"][int(path[5]) % 7]
        return ("data:image/svg+xml;utf8," + urllib.parse.quote(
            f"<svg xmlns='http://www.w3.org/2000/svg' width='200' height='300'><rect width='200' height='300' "
            f"fill='{col}'/><text x='20' y='280' fill='white' font-size='22' font-family='sans-serif'>REAL</text></svg>"))
    return f"https://image.tmdb.org/t/p/{POSTER_WIDTHS.get(size.split()[0], 'w342')}{path}"


def prefetch_posters(items):
    """When real posters are switched on, look up the films about to be shown."""
    if want_real_posters() and items:
        remember_posters(film_infos(items))


def settings_menu():
    """Per-person settings: poster style and streaming country. Hidden entirely when there's nothing to set."""
    if not tmdb_on():
        return
    with st.popover("Settings", icon=":material/tune:"):
        st.segmented_control("Posters", ["art", "real"], default="art",
                             key="poster_style", format_func={"art": "Minimal art", "real": "Real posters"}.get)
        region = current_region()
        st.selectbox("Streaming in", list(REGIONS), index=list(REGIONS).index(region),
                      format_func=REGIONS.get, key="region")


@st.cache_resource
def _film_store():
    return {"lock": threading.Lock(), "films": {}}


def _tmdb_get(path, **params):
    key = str(_secret("TMDB_API_KEY") or "")
    headers = {"accept": "application/json"}
    if key.startswith("eyJ"):  # the long "API read access token"
        headers["Authorization"] = f"Bearer {key}"
    else:                      # the short "API key"
        params["api_key"] = key
    try:
        r = requests.get(TMDB_API + path, params=params, headers=headers, timeout=6)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def _clean_service(name: str) -> str:
    for junk in (" Standard with Ads", " with Ads", " Amazon Channel", " Apple TV Channel"):
        name = name.replace(junk, "")
    return name.replace("Amazon Prime Video", "Prime Video").replace("Apple TV Plus", "Apple TV+") \
               .replace("Disney Plus", "Disney+")


def _fake_info(name, year):
    h = zlib.crc32(f"{name}|{year}".encode())
    genres = ["Drama", "Comedy", "Thriller", "Romance", "Horror", "Sci-Fi", "Animation", "Crime", "Documentary"]
    services = ["Netflix", "Prime Video", "MUBI", "BBC iPlayer", "Disney+", "Apple TV+"]
    stream = [services[(h >> s) % len(services)] for s in (3, 9)][: (h % 3)]
    return {"id": h % 100000, "poster": f"/fake{h % 7}.jpg", "runtime": 80 + h % 100, "genres": [genres[h % 9], genres[(h // 9) % 9]][: 1 + h % 2],
            "providers": {cc: {"stream": sorted(set(stream)), "rent": bool(h % 2), "link": ""} for cc in REGIONS}}


def _fetch_info(name, year):
    """Look a film up on TMDB. Returns a dict, {} if not found, or None if TMDB couldn't be reached."""
    if FAKE_TMDB:
        return _fake_info(name, year)
    # Search both with and without the year and pool the results. Letterboxd often dates a film by its
    # festival premiere while TMDB uses the cinema release (or the other way round), so a year-only
    # search can miss the real film and land on a short with the same name.
    by_year = _tmdb_get("/search/movie", query=name, year=year) if year else None
    plain = _tmdb_get("/search/movie", query=name)
    if by_year is None and plain is None:
        return None
    hits = list({h["id"]: h for r in (plain, by_year) if r for h in (r.get("results") or []) if h.get("id")}.values())
    if not hits:
        return {}
    want = title_key(name)

    def rank(h):
        same = want in (title_key(h.get("title", "")), title_key(h.get("original_title", "")))
        y = (h.get("release_date") or "")[:4]
        off = abs(int(y) - int(year)) if (year and y.isdigit()) else 5
        # A year either side counts as a match; among those, the film people have actually seen wins.
        return (not same, off > 1, -(h.get("vote_count") or 0), off, -(h.get("popularity") or 0))

    best = sorted(hits, key=rank)[0]
    det = _tmdb_get(f"/movie/{best['id']}") or {}
    prov = (_tmdb_get(f"/movie/{best['id']}/watch/providers") or {}).get("results") or {}
    providers = {}
    for cc, v in prov.items():
        streams = sorted(v.get("flatrate", []) + v.get("free", []) + v.get("ads", []),
                         key=lambda p: p.get("display_priority", 99))
        names = list(dict.fromkeys(_clean_service(p.get("provider_name", "")) for p in streams if p.get("provider_name")))
        providers[cc] = {"stream": names, "rent": bool(v.get("rent") or v.get("buy")), "link": v.get("link") or ""}
    return {"id": best["id"], "poster": best.get("poster_path") or det.get("poster_path"),
            "runtime": det.get("runtime") or None,
            "genres": [g["name"] for g in det.get("genres", [])][:3], "providers": providers}


DETAIL_VERSION = 2  # bump when the lookup changes, so details cached by older code are fetched again


def _detail_key(name, year):
    return f"v{DETAIL_VERSION}|{title_key(name)}|{year}"


def film_infos(items, limit=150):
    """items: [(key, name, year)]. Returns {key: info}, fetching anything new in parallel."""
    if not tmdb_on() or not items:
        return {}
    store, now, out, todo = _film_store(), time.time(), {}, []
    offline = store.get("down_until", 0) > now  # TMDB failing (bad key or outage): don't keep retrying
    for k, name, year in items:
        y = _norm_year(year)
        ck = _detail_key(name, y)
        hit = store["films"].get(ck)
        if hit and now - hit[0] < DETAIL_TTL:
            out[k] = hit[1]
        else:
            todo.append((k, name, y, ck))
    todo = [] if offline else todo[:limit]
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool_:
            results = list(pool_.map(lambda t: _fetch_info(t[1], t[2]), todo))
        with store["lock"]:
            if len(store["films"]) > 5000:
                store["films"].clear()
            for (k, _, _, ck), info in zip(todo, results):
                if info is not None:
                    store["films"][ck] = (now, info)
                    out[k] = info
            if all(r is None for r in results):
                store["down_until"] = now + 600  # try again in 10 minutes
    return out


def fmt_runtime(m) -> str:
    if not m:
        return ""
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def details_text(info) -> str:
    if not info:
        return ""
    return " · ".join(x for x in (fmt_runtime(info.get("runtime")), ", ".join(info.get("genres", [])[:2])) if x)


def services(info, region):
    return ((info or {}).get("providers") or {}).get(region, {}).get("stream", [])


def stream_text(info, region) -> str:
    p = ((info or {}).get("providers") or {}).get(region)
    if not p:
        return ""
    if p["stream"]:
        more = f" +{len(p['stream']) - 2}" if len(p["stream"]) > 2 else ""
        return "Stream on " + ", ".join(p["stream"][:2]) + more
    return "Rent or buy" if p["rent"] else ""


def info_lines_html(info, region) -> str:
    d, s = details_text(info), stream_text(info, region)
    return (f'<div class="tk-meta">{esc(d)}</div>' if d else "") + (f'<div class="tk-stream">{esc(s)}</div>' if s else "")


def attribution():
    if tmdb_on():
        md('<div class="credits">Film details from <a href="https://www.themoviedb.org" target="_blank">TMDB</a>. '
           'Streaming info from <a href="https://www.justwatch.com" target="_blank">JustWatch</a>. '
           'Not endorsed or certified by TMDB.</div>')


# ---------- Share images: a picture of the result to send to friends ----------

SHARE_FONTS = Path(__file__).parent / "static" / "share"


@functools.lru_cache(maxsize=64)
def _font(name, size):
    """Share-image font. If the font file is missing (e.g. static/share wasn't deployed), fall back to
    Pillow's built-in font rather than crashing the page."""
    try:
        return ImageFont.truetype(str(SHARE_FONTS / f"{name}.ttf"), size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            return ImageFont.load_default()


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _spaced(draw, xy_center, text, font, fill, spacing):
    """Draw letter-spaced text centred on x."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = xy_center[0] - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, xy_center[1]), ch, font=font, fill=fill)
        x += w + spacing


def _wrap(draw, text, font, max_w, max_lines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while draw.textlength(lines[-1] + "…", font=font) > max_w and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def _poster_image(k, name, w, h, year=None):
    """The same screen-print poster as in the app, drawn with Pillow."""
    genres = _known_genres(name, year, k)
    _, pal, _ = poster_design(str(k), str(name), genres)
    top, bottom, _, ink = (_hex(x) for x in PALETTES[pal])
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):  # vertical gradient
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    img, _ = draw_poster_art(img, str(k), str(name), genres)
    d = ImageDraw.Draw(img)
    f = _font("fraunces-600", int(w * .11))
    y = int(h * .07)
    for line in _wrap(d, name, f, w * .82, 4):
        d.text((w * .09, y), line, font=f, fill=ink)
        y += int(w * .125)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(w * .04), fill=255)
    out = Image.new("RGBA", (w, h))
    out.paste(img, (0, 0), mask)
    return out


def _share_canvas(kicker):
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), _hex("#111926"))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * .05, -H * .35, W * .95, H * .35], fill=(242, 193, 78, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    img.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(img)
    # marquee bulbs around the edge
    m, step = 34, 30
    for x in range(m, W - m + 1, step):
        for y in (m, H - m):
            d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(255, 236, 180))
    for y in range(m + step, H - m, step):
        for x in (m, W - m):
            d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(255, 236, 180))
    d.rounded_rectangle([m + 22, m + 22, W - m - 22, H - m - 22], radius=26, outline=(242, 193, 78), width=2)
    _spaced(d, (W / 2, 104), kicker.upper(), _font("bebas-neue", 46), _hex(GOLD), 10)
    _spaced(d, (W / 2, H - 132), "DOUBLE FEATURE", _font("bebas-neue", 34), _hex(CREAM), 8)
    f = _font("dm-sans-500", 24)
    url = "double-feature.streamlit.app"
    d.text((W / 2 - d.textlength(url, font=f) / 2, H - 88), url, font=f, fill=_hex(MUTED))
    return img, d


@st.cache_data(show_spinner=False, max_entries=200)
def _fetch_poster(url, w, h):
    """Download a real poster for a share image, cropped to fill w×h with rounded corners."""
    if not url.startswith("https://"):
        return None
    try:
        r = requests.get(url, timeout=6)
        src = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return None
    scale = max(w / src.width, h / src.height)
    src = src.resize((int(src.width * scale) + 1, int(src.height * scale) + 1), Image.LANCZOS)
    left, top_ = (src.width - w) // 2, (src.height - h) // 2
    src = src.crop((left, top_, left + w, top_ + h))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(w * .04), fill=255)
    out = Image.new("RGBA", (w, h))
    out.paste(src, (0, 0), mask)
    return out


def share_film_png(kicker, names_line, k, name, year, meta, stream, poster_url=""):
    img, d = _share_canvas(kicker)
    W = img.width
    if names_line:
        f = _font("fraunces-italic", 48)
        d.text((W / 2 - d.textlength(names_line, font=f) / 2, 168), names_line, font=f, fill=_hex(CREAM))
    pw, ph = 440, 660
    shadow = Image.new("RGBA", (pw + 120, ph + 120), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([60, 80, pw + 60, ph + 80], radius=24, fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    img.paste(shadow, (int(W / 2 - pw / 2 - 60), 250 - 60), shadow)
    poster_ = (_fetch_poster(poster_url, pw, ph) if poster_url else None) or _poster_image(k, name, pw, ph, year)
    img.paste(poster_, (int(W / 2 - pw / 2), 250), poster_)
    y = 950
    ft = _font("fraunces-600", 66)
    for line in _wrap(d, name, ft, W - 220, 2):
        d.text((W / 2 - d.textlength(line, font=ft) / 2, y), line, font=ft, fill=_hex(CREAM))
        y += 76
    sub = " · ".join(x for x in (str(year or ""), meta) if x)
    if sub:
        _spaced(d, (W / 2, y + 10), sub.upper(), _font("bebas-neue", 36), _hex(MUTED), 4)
        y += 56
    if stream:
        f = _font("dm-sans-500", 30)
        d.text((W / 2 - d.textlength(stream, font=f) / 2, y + 10), stream, font=f, fill=_hex(GOLD))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=100)
def share_taste_png(score, verdict, a, b, n_both, gap, tougher):
    img, d = _share_canvas("Taste match")
    W = img.width
    f = _font("fraunces-italic", 52)
    names = f"{a} & {b}"
    d.text((W / 2 - d.textlength(names, font=f) / 2, 170), names, font=f, fill=_hex(CREAM))
    cx, cy, R, wd = W / 2, 560, 250, 34
    box = [cx - R, cy - R, cx + R, cy + R]
    d.ellipse(box, outline=(43, 52, 68), width=wd)
    gold, sea = _hex(GOLD), _hex(SEA)
    end = int(360 * score / 100)
    for i in range(end):  # gold fading to sea-green, like the ring in the app
        t = i / max(end, 1)
        col = tuple(int(g + (s - g) * t) for g, s in zip(gold, sea))
        d.arc(box, start=-90 + i, end=-90 + i + 1.5, fill=col, width=wd)
    big = _font("fraunces-600", 190)
    txt = f"{score}%"
    d.text((cx - d.textlength(txt, font=big) / 2, cy - 130), txt, font=big, fill=_hex(CREAM))
    fv = _font("fraunces-italic", 64)
    d.text((W / 2 - d.textlength(verdict, font=fv) / 2, 860), verdict, font=fv, fill=_hex(GOLD))
    stats = [(str(n_both), "BOTH RATED"), (f"{gap:.1f}", "AVG STAR GAP"), (tougher, "TOUGHER CRITIC")]
    for i, (v, lab) in enumerate(stats):
        x = W / 2 + (i - 1) * 300
        fv2 = _font("fraunces-600", 58 if len(v) < 9 else 40)
        d.text((x - d.textlength(v, font=fv2) / 2, 990), v, font=fv2, fill=_hex(CREAM))
        _spaced(d, (x, 1068), lab, _font("bebas-neue", 28), _hex(MUTED), 3)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


SHARE_JS = """
export default function(component) {
  const { data, parentElement } = component;
  let btn = parentElement.querySelector('button.share-btn');
  if (!btn) { btn = document.createElement('button'); btn.className = 'share-btn'; parentElement.appendChild(btn); }
  btn.innerHTML = data.label;
  // Build the file now, so tapping can call the share sheet straight away (phones require that)
  const bin = atob(data.img);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const file = new File([bytes], data.filename, { type: 'image/jpeg' });
  btn.onclick = async () => {
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], text: data.text }); return; }
      catch (e) { if (e && e.name === 'AbortError') return; }
    }
    const url = URL.createObjectURL(file);
    const a = document.createElement('a'); a.href = url; a.download = data.filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };
}
"""
sharer = st.components.v2.component("share_image", js=SHARE_JS, isolate_styles=False)

ICON_SHARE = ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M7 8l5-5 5 5M5 13v6a2 2 0 0 0 2 2h10a2 2 '
              '0 0 0 2-2v-6"/></svg>')


def share_button(make_image, filename, text, key, label="Share"):
    """make_image is called here so that if drawing the image ever fails, the button is skipped
    instead of the whole page showing an error."""
    try:
        img_bytes = make_image() if callable(make_image) else make_image
    except Exception:
        return
    sharer(key=key, data={"img": base64.b64encode(img_bytes).decode(), "filename": filename, "text": text,
                          "label": f"{ICON_SHARE}<span>{esc(label)}</span>"})


DETAILS_CSS = """
<style>
.tk-meta { font-size: .8rem; margin-top: .45rem; opacity: .8; }
.tk-stream { font-size: .8rem; font-weight: 600; margin-top: .2rem; }
.sw-meta { font-size: .78rem; opacity: .85; margin-top: .15rem; }
.sw-stream { font-size: .78rem; color: var(--gold); font-weight: 600; margin-top: .1rem; }
.share-btn { display: flex; align-items: center; justify-content: center; gap: .5rem; width: 100%; min-height: 2.9rem;
  border-radius: 999px; border: 1.5px solid rgba(242,193,78,.6); background: rgba(242,193,78,.08); color: var(--gold);
  font-family: var(--label); font-size: 1.15rem; letter-spacing: .14em; padding-top: .15rem; cursor: pointer;
  -webkit-tap-highlight-color: transparent; }
.share-btn:active { transform: scale(.98); }
.credits { color: var(--muted); font-size: .72rem; text-align: center; margin-top: 2.2rem; opacity: .8; }
.credits a { color: var(--muted) !important; }
.sw-super { width: 54px; height: 54px; align-self: center; background: rgba(242,193,78,.08); color: var(--gold);
  box-shadow: inset 0 0 0 2px rgba(242,193,78,.6); }
.sw-super[disabled] { opacity: .3; cursor: default; }
.stamp-super { left: 50%; top: 18%; transform: translateX(-50%) rotate(-6deg); color: var(--gold); border-color: var(--gold);
  white-space: nowrap; font-size: 2rem; }
.super-tag { color: var(--gold); font-size: .78rem; margin-left: .35rem; }
</style>
"""
md(DETAILS_CSS)


# ---------- Swipe night: shared rooms so two phones can swipe on the same deck ----------
# Rooms live in server memory (one dict shared by every session), keyed by a 4-letter code.
# Nothing personal is stored: just the two display names, the film list and the yes/no votes.

ROOM_TTL = 12 * 3600
MAX_ROOMS = 300
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
    # Hard cap: if there are ever more than MAX_ROOMS live, drop the oldest
    for c in sorted(store["rooms"], key=lambda c: store["rooms"][c]["created"])[:-MAX_ROOMS or None]:
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
  const sup = card.querySelector('.stamp-super');
  let sx = 0, sy = 0, dx = 0, dy = 0, dragging = false, done = false;
  const send = (like) => {
    if (done) return; done = true;
    card.style.transition = 'transform .38s ease-in, opacity .38s ease-in';
    card.style.transform = like === 'super' ? 'translate(0, -720px) scale(.9)'
      : `translate(${like ? 560 : -560}px, ${dy * 1.5}px) rotate(${like ? 28 : -28}deg)`;
    card.style.opacity = '0';
    (like === 'super' ? sup : like ? yes : no).style.opacity = 1;
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
  const sb = root.querySelector('.sw-super');
  if (sb && !sb.disabled) sb.onclick = () => send('super');
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
    meta = f'<div class="sw-meta">{esc(c["meta"])}</div>' if c.get("meta") else ""
    if real_poster_url(c["key"], "xl"):  # real posters: put the title in the caption too
        meta = f'<div class="sw-title">{esc(c["name"])}</div>' + meta
    strm = f'<div class="sw-stream">{esc(c["stream"])}</div>' if c.get("stream") else ""
    return (f'<div class="sw-card {cls}" data-key="{esc(c["key"])}">{art}'
            f'<div class="stamp stamp-yes">Watch</div><div class="stamp stamp-no">Pass</div>'
            f'<div class="stamp stamp-super">Must watch</div>'
            f'<div class="sw-info"><div><div><b>{esc(str(c["year"] or ""))}</b> · {esc(c["why"])}</div>{meta}{strm}</div>'
            f'{lb}</div></div>')


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
        note("<b>That swipe session has ended.</b> Sessions last up to 12 hours. "
             "Start a new one from the Swipe tab.")
        st.button("OK", on_click=leave_room)
        return
    names = room["names"]
    COLOURS.update({names[0]: GOLD, names[1]: SEA})
    seat = st.session_state.get(f"seat_{code}")
    prefetch_posters([(c["key"], c["name"], c["year"]) for c in room["deck"]])

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
        why = esc(c["why"]) + "".join(f'<div class="{cls}">{esc(c[f])}</div>'
                                      for f, cls in (("meta", "tk-meta"), ("stream", "tk-stream")) if c.get(f))
        md(ticket(c["key"], c["name"], c["year"], c["uri"], why, kicker="Matched for tonight"))
        share_button(lambda: share_film_png("It's a match", f"{names[0]} & {names[1]}", c["key"], c["name"], c["year"],
                                    c.get("meta", ""), c.get("stream", ""), real_poster_url(c["key"], "xl")),
                     "double-feature-match.jpg", f"It's a match: {c['name']} 🎬", key=f"share_match_{code}",
                     label="Share the match")
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
.or { display: flex; align-items: center; gap: .8rem; margin: 1.4rem 0 .2rem; color: var(--muted);
  font-family: var(--label); letter-spacing: .2em; font-size: .9rem; }
.or:before, .or:after { content: ""; flex: 1; height: 1px; background: var(--line); }
.how-n { color: var(--muted); font-size: .8rem; margin: -.3rem 0 .2rem; }
.how { display: grid; gap: 8px; margin: .8rem 0 1.1rem; }
.how div { display: flex; gap: .8rem; align-items: center; border-radius: 14px; padding: .75rem .9rem;
  background: rgba(27,37,54,.75); box-shadow: inset 0 0 0 1px var(--line); font-size: .9rem; line-height: 1.4; }
.how > div > b { flex: 0 0 1.9rem; height: 1.9rem; border-radius: 50%; display: grid; place-items: center; font-family: var(--label);
  font-size: 1.05rem; font-weight: 400; background: rgba(242,193,78,.15); color: var(--gold); padding-top: .1rem; }
@media (prefers-reduced-motion: reduce) { .sw-card.top, .its-match, .live { animation: none !important; } }
</style>
"""
md(SWIPE_CSS)


# ---------- Uploads (or joining someone else's swipe session) ----------

KEEP = {  # the only parts of an export the app ever reads
    "watched.csv": ["Name", "Year", "Letterboxd URI"],
    "ratings.csv": ["Name", "Year", "Letterboxd URI", "Rating"],
    "watchlist.csv": ["Name", "Year", "Letterboxd URI"],
    "profile.csv": ["Username", "Given Name"],
}


def bundle(files):
    """Turn an upload into a slimmed-down zip holding only what the app uses.

    Accepts the export zip itself or loose CSVs from an unzipped export. Everything else in the
    export (email address, reviews, comments, diary notes, etc.) is dropped here, straight after upload.
    """
    files = files or []
    src = {}
    zips = [f for f in files if f.name.lower().endswith(".zip")]
    try:
        if zips:
            z = zipfile.ZipFile(io.BytesIO(zips[0].getvalue()))
            for name in KEEP:
                path = _find(z, name)
                if path:
                    src[name] = z.read(path)
        else:
            for f in files:
                name = f.name.rsplit("/", 1)[-1].lower()
                if name in KEEP:
                    src[name] = f.getvalue()
    except zipfile.BadZipFile:
        return None
    if not src:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in src.items():
            try:
                df = pd.read_csv(io.BytesIO(data), dtype=str)
            except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError):
                continue
            df = df[[c for c in KEEP[name] if c in df.columns]]
            out.writestr(name, df.to_csv(index=False))
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
    elif room["kind"] == "group":
        st.session_state.group_join = code
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


def code_card(code, text, label="Pair code"):
    link = f"{st.context.url.split('?')[0]}?room={code}" if st.context.url else ""
    md(f'<div class="room"><div class="room-k">{label}</div><div class="room-code">{code}</div>'
       f'<div class="room-n">{text}</div></div>')
    if link:
        st.code(link, language=None)


def join_box():
    md('<div class="or"><span>or</span></div>')
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    c1.text_input("Have a code?", key="join_code", max_chars=4, placeholder="ABCD")
    c2.button("Join", width="stretch", on_click=join_room)
    if st.session_state.get("join_error"):
        st.caption(st.session_state.join_error)


UPLOAD_HELP = ("On letterboxd.com (not the phone app) go to Settings → Data → Export your data. "
               "Upload the .zip, or the watched, ratings and watchlist CSVs inside it.")
NAME_HELP = "Leave blank to use the name on your Letterboxd profile."
NAME_HINT = "Optional"


EXPORT_URL = "https://letterboxd.com/settings/data/"


def export_guide(group=False, expanded=False):
    """A one-tap link to Letterboxd's export page plus illustrated steps (drawn, so they never go stale)."""
    with st.expander("How do I get my Letterboxd export?", expanded=expanded):
        st.link_button("Open Letterboxd's export page", EXPORT_URL, icon=":material/open_in_new:", width="stretch")
        steps = []
        if group:
            steps.append((
                '<div class="gm-top">New list</div><div class="gm-in">Tonight\'s picks</div>'
                '<div class="gm-row"></div><div class="gm-row s"></div><div class="gm-row"></div>'
                '<div class="gm-save">Save</div>',
                "<b>Make a list first.</b> On Letterboxd, make a list of the 5–10 films you'd happily watch "
                "tonight. Do this <i>before</i> exporting, or it won't be in the export."))
        steps += [
            ('<div class="gm-url"><span>🔒</span>letterboxd.com/settings/data</div>'
             '<div class="gm-in">Username</div><div class="gm-in">Password</div><div class="gm-save">Sign in</div>',
             "<b>Open the export page</b> with the button above. It opens in your browser (the Letterboxd phone "
             "app can't export). Sign in if it asks."),
            ('<div class="gm-top">Settings</div><div class="gm-tabs"><span>Profile</span><span>Auth</span>'
             '<span class="on">Data</span></div><div class="gm-btn"><i></i>Export your data</div>',
             "<b>Tap Export your data</b> on the Data tab, then confirm. Letterboxd builds your file in a few "
             "seconds."),
            ('<div class="gm-file"><span class="gm-zip">ZIP</span><div><b>letterboxd-you.zip</b>'
             '<small>Downloaded</small></div></div><div class="gm-arrow">↓ upload it below</div>',
             "<b>Come back here and upload the .zip.</b> On iPhone it's in Files → Downloads, on Android in "
             "Downloads. If it unzipped itself, upload the CSV files inside instead."),
        ]
        md('<div class="guide">' + "".join(
            f'<div class="gstep"><div class="gmock">{mock}</div><div class="gtext"><em>{i}</em>{text}</div></div>'
            for i, (mock, text) in enumerate(steps, 1)) + "</div>")


GUIDE_CSS = """
<style>
.guide { display: grid; gap: 10px; margin-top: .7rem; }
.gstep { display: grid; grid-template-columns: 118px 1fr; gap: .8rem; align-items: center; padding: .65rem;
  border-radius: 14px; background: rgba(17,25,38,.6); box-shadow: inset 0 0 0 1px var(--line); }
.gtext { font-size: .86rem; line-height: 1.45; color: var(--muted); }
.gtext b { color: var(--cream); font-weight: 600; }
.gtext em { display: inline-grid; place-items: center; width: 1.35rem; height: 1.35rem; border-radius: 50%; font-style: normal;
  background: rgba(242,193,78,.15); color: var(--gold); font-family: var(--label); font-size: .9rem; margin-right: .4rem;
  padding-top: .1rem; vertical-align: .05rem; }
.gmock { border-radius: 12px; background: #e9e6df; padding: .45rem .45rem .5rem; color: #2a2f38; font-size: .56rem;
  line-height: 1.2; box-shadow: 0 0 0 3px #2a3448, 0 8px 18px -8px rgba(0,0,0,.8); min-height: 92px;
  display: flex; flex-direction: column; gap: .28rem; overflow: hidden; }
.gm-url { display: flex; gap: .2rem; align-items: center; background: #fff; border-radius: 6px; padding: .2rem .3rem;
  font-size: .5rem; color: #555; white-space: nowrap; overflow: hidden; }
.gm-top { font-weight: 700; font-size: .66rem; }
.gm-in { background: #fff; border-radius: 4px; padding: .2rem .3rem; color: #999; border: 1px solid #d5d1c8; }
.gm-row { height: .5rem; border-radius: 3px; background: #c9c3b6; width: 85%; }
.gm-row.s { width: 60%; }
.gm-save { align-self: flex-start; background: #2a3448; color: #fff; border-radius: 4px; padding: .18rem .45rem;
  font-weight: 700; }
.gm-tabs { display: flex; gap: .35rem; border-bottom: 1px solid #cfc9bd; padding-bottom: .2rem; color: #888; }
.gm-tabs .on { color: #2a2f38; font-weight: 700; box-shadow: 0 .25rem 0 -.08rem var(--gold-deep); }
.gm-btn { position: relative; align-self: center; margin-top: .35rem; background: #2a3448; color: #fff; border-radius: 6px;
  padding: .35rem .5rem; font-weight: 700; white-space: nowrap; }
.gm-btn i { position: absolute; right: -.35rem; bottom: -.4rem; width: 1rem; height: 1rem; border-radius: 50%;
  background: rgba(242,193,78,.55); box-shadow: 0 0 0 0 rgba(242,193,78,.7); animation: tapping 1.6s infinite; }
@keyframes tapping { 70% { box-shadow: 0 0 0 .55rem rgba(242,193,78,0); } 100% { box-shadow: 0 0 0 0 rgba(242,193,78,0); } }
.gm-file { display: flex; gap: .35rem; align-items: center; background: #fff; border-radius: 8px; padding: .35rem;
  margin-top: .3rem; }
.gm-file b { display: block; font-size: .55rem; } .gm-file small { color: #3a8a5a; font-weight: 600; }
.gm-zip { background: var(--gold); color: #2a2f38; border-radius: 4px; padding: .3rem .25rem; font-weight: 800; font-size: .5rem; }
.gm-arrow { text-align: center; color: #b07a12; font-weight: 700; margin-top: .15rem; }
@media (prefers-reduced-motion: reduce) { .gm-btn i { animation: none; } }
</style>
"""
md(GUIDE_CSS)


def how_it_works(pairing=True, group=False):
    with st.expander("How it works"):
        steps = [
            "The host starts a movie night and shares the code or link.",
            "Everyone joins on their own phone and brings 5–10 films: make a Letterboxd list, then upload your "
            "Letterboxd export (Settings → Data → Export your data) and pick that list.",
            "Everyone swipes, with one super-like each that counts double. The film most people want wins, "
            "and you get a shortlist of the top five.",
        ] if group else [
            "Export your Letterboxd data on the website (not the phone app). "
            "\"How do I get my Letterboxd export?\" below walks you through it.",
            ("One of you uploads theirs and gets a pair code. The other enters the code (or opens the link) "
             "and uploads theirs." if pairing else "Upload both exports here."),
            "Then pick a film at random, swipe to match, see your taste match, and find films to show each other.",
        ]
        md('<div class="how">' + "".join(f"<div><b>{i}</b><span>{t}</span></div>" for i, t in enumerate(steps, 1)) + "</div>"
           '<div class="how-n">Uploads stay in the app\'s memory for up to 12 hours and are never saved to disk.</div>')

# ---------- Group movie night: everyone brings films, everyone swipes, the most-wanted film wins ----------
# A group room holds the guest list, the films each person brought, and yes/no votes, in server memory.

GROUP_MAX_FILMS = 20   # per person
GROUP_UPLOAD_HELP = ("Make a list of the films you'd watch tonight on Letterboxd, then export your data from "
                     "letterboxd.com → Settings → Data (after making the list). Upload the .zip and pick the list, "
                     "or upload just that list's CSV.")
GROUP_MAX_PEOPLE = 16


def _norm_year(y):
    y = pd.to_numeric(y, errors="coerce")
    return None if pd.isna(y) else int(y)


def title_key(name: str) -> str:
    """Forgiving title match: ignore case, accents, punctuation and '&' vs 'and'."""
    t = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().casefold()
    t = re.sub(r"[^a-z0-9]+", " ", t.replace("&", " and "))
    return " ".join(t.split())


def film_entry(name, year=None, uri=None):
    name = str(name).strip()
    year = _norm_year(year)
    key = f"{title_key(name) or name.casefold()}|{year if year is not None else '<NA>'}"
    if not uri or (isinstance(uri, float) and pd.isna(uri)):
        uri = f"https://letterboxd.com/search/films/{urllib.parse.quote(name)}/"
    return {"key": key, "name": name, "year": str(year or ""), "uri": str(uri)}


def parse_list_csv(data: bytes):
    """Read a Letterboxd list CSV (or any CSV with Name and Year columns), skipping the list's header block."""
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if {"name", "year"} <= {c.strip().strip('"').lower() for c in l.split(",")}), None)
    if start is None:
        return []
    try:
        df = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str)
    except (pd.errors.ParserError, pd.errors.EmptyDataError):
        return []
    if "Name" not in df.columns:
        return []
    uri_col = next((c for c in ("Letterboxd URI", "URL") if c in df.columns), None)
    return [film_entry(r["Name"], r.get("Year"), r.get(uri_col) if uri_col else None)
            for _, r in df.dropna(subset=["Name"]).iterrows()]


def lists_in_export(raw: bytes):
    """From a full Letterboxd export zip: {display name: films} for each list, plus the watchlist."""
    out = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return out
    skip = {"deleted", "orphaned", "likes"}
    for n in sorted(z.namelist()):
        parts = n.split("/")
        if n.lower().endswith(".csv") and "lists" in parts[:-1] and not skip.intersection(parts[:-1]):
            films = parse_list_csv(z.read(n))
            if films:
                out[parts[-1][:-4].replace("-", " ").strip().capitalize()] = films
    wl = _find(z, "watchlist.csv")
    if wl:
        films = parse_list_csv(z.read(wl))
        if films:
            out["My watchlist"] = films
    return out


def create_group(host_name) -> tuple:
    store = _room_store()
    with store["lock"]:
        code = _new_code(store)
        mid = secrets.token_hex(4)
        store["rooms"][code] = {"code": code, "kind": "group", "created": time.time(), "host": mid,
                                "members": {mid: {"name": host_name, "films": []}}, "films": {},
                                "phase": "lobby", "deck": [], "votes": {}, "round": 1}
    return code, mid


def group_state(room):
    """Changes here reload every phone in the group (phase changes and new rounds)."""
    return (room["phase"], room["round"])


def add_member(code, name):
    store = _room_store()
    with store["lock"]:
        room = get_room(code)
        if not room or len(room["members"]) >= GROUP_MAX_PEOPLE:
            return None
        taken = {m["name"].casefold() for m in room["members"].values()}
        base, n = name, 2
        while name.casefold() in taken:
            name, n = f"{base} {n}", n + 1
        mid = secrets.token_hex(4)
        room["members"][mid] = {"name": name, "films": []}
        if room["phase"] == "voting":
            room["votes"][mid] = {}
        return mid


def set_films(code, mid, films):
    store = _room_store()
    with store["lock"]:
        room = get_room(code)
        if not room or mid not in room["members"] or room["phase"] != "lobby":
            return
        me_ = room["members"][mid]
        for f in me_["films"]:  # replace this person's previous picks
            entry = room["films"].get(f)
            if entry:
                entry["by"] = [b for b in entry["by"] if b != mid]
                if not entry["by"]:
                    del room["films"][f]
        me_["films"] = []
        for f in films[:GROUP_MAX_FILMS]:
            title = f["key"].split("|")[0]
            if f["key"].endswith("|<NA>"):  # typed without a year: match a film someone else brought
                same = [k for k in room["films"] if k.split("|")[0] == title]
                if same:
                    f = room["films"][same[0]]
            elif f"{title}|<NA>" in room["films"]:  # someone typed this one without a year: upgrade it
                old = room["films"].pop(f"{title}|<NA>")
                room["films"][f["key"]] = {**f, "by": old["by"]}
                for m in room["members"].values():
                    m["films"] = [f["key"] if k == old["key"] else k for k in m["films"]]
            entry = room["films"].setdefault(f["key"], {**f, "by": []})
            if mid not in entry["by"]:
                entry["by"].append(mid)
                me_["films"].append(f["key"])


def start_group_vote(code, keys=None):
    store = _room_store()
    with store["lock"]:
        room = get_room(code)
        if not room:
            return
        deck = list(keys) if keys else list(room["films"])
        random.Random(f"{code}{room['round']}").shuffle(deck)
        room["deck"], room["phase"] = deck, "voting"
        room["votes"] = {m: {} for m in room["members"]}
        room["supers"] = {}  # one super-like per person per round
        room["round"] += 1


def end_group_vote(code):
    with _room_store()["lock"]:
        room = get_room(code)
        if room and room["phase"] == "voting":
            room["phase"] = "results"


def back_to_lobby(code):
    with _room_store()["lock"]:
        room = get_room(code)
        if room:
            room["phase"], room["deck"], room["votes"] = "lobby", [], {}
            room["round"] += 1


def group_vote(code, mid):
    v = (st.session_state.get(f"gdeck_{code}") or {}).get("swipe")
    if not (v and v.get("key")):
        return
    with _room_store()["lock"]:
        room = get_room(code)
        if not room or room["phase"] != "voting" or v["key"] not in room["deck"]:
            return
        like = v.get("like")
        if like == "super":
            supers = room.setdefault("supers", {})
            if mid in supers:  # already used this round: count it as a normal yes
                like = True
            else:
                supers[mid] = v["key"]
        room["votes"].setdefault(mid, {})[v["key"]] = like if like == "super" else bool(like)
        voters = [m for m in room["votes"] if m in room["members"]]
        if voters and all(len(room["votes"][m]) >= len(room["deck"]) for m in voters):
            room["phase"] = "results"


def tally(room):
    """Rank films by points (a yes is 1, a super-like is 2), then fewest no votes.
    Returns [(key, yes, no, supers, points)]."""
    rows = []
    for k in room["deck"]:
        vals = [v.get(k) for v in room["votes"].values()]
        yes = sum(1 for x in vals if x is True or x == "super")
        sup = sum(1 for x in vals if x == "super")
        no = sum(1 for x in vals if x is False)
        rows.append((k, yes, no, sup, yes + sup))
    tie = random.Random(f"{room['code']}{room['round']}")
    rows.sort(key=lambda r: (-r[4], r[2], tie.random()))
    return rows


def leave_group():
    for k in ("group", "gid", "group_join"):
        st.session_state.pop(k, None)
    st.query_params.pop("room", None)


def host_group():
    name = st.session_state.get("gh_name", "").strip() or "Host"
    code, mid = create_group(name)
    st.session_state.group, st.session_state.gid = code, mid


def join_group(code):
    name = st.session_state.get("gj_name", "").strip()
    if not name:
        st.session_state.gj_error = "Add your name so everyone knows who's voting."
        return
    mid = add_member(code, name)
    if not mid:
        st.session_state.gj_error = "That movie night is full or has ended."
        return
    st.session_state.gj_error = ""
    st.session_state.group, st.session_state.gid = code, mid
    st.session_state.pop("group_join", None)


def rejoin_group(code, mid):
    st.session_state.group, st.session_state.gid = code, mid
    st.session_state.pop("group_join", None)


def save_my_films(code, mid, films):
    set_films(code, mid, films)
    st.session_state.gf_saved = True


def by_names(room, entry):
    names = [room["members"][m]["name"] for m in entry["by"] if m in room["members"]]
    return "Brought by " + (", ".join(names) if names else "someone")


@st.fragment(run_every=2)
def group_pulse(code, mid, seen):
    room = get_room(code)
    if not room or group_state(room) != seen:
        st.rerun(scope="app")
    members = room["members"]
    if room["phase"] == "lobby":
        chips = "".join(
            f'<div class="gm"><span class="gm-n">{esc(m["name"])}{" ★" if k == room["host"] else ""}</span>'
            f'<span class="gm-c">{len(m["films"]) or "no"} film{"s" if len(m["films"]) != 1 else ""}</span></div>'
            for k, m in members.items())
        md(f'<div class="sec-k" style="margin-top:1.2rem">Who\'s coming · {len(members)}</div>'
           f'<div class="gms">{chips}</div>')
        n = len(room["films"])
        if mid == room["host"]:
            st.button(f"Start the vote · {n} films", type="primary", width="stretch", disabled=n < 2,
                      on_click=start_group_vote, args=(code,), key="g_start")
            if n < 2:
                st.caption("You need at least two films in the pot to start.")
        else:
            host = members[room["host"]]["name"]
            md(f'<div class="sw-status"><span class="live"></span>Waiting for&nbsp;<b>{esc(host)}</b>to start the vote'
               f' · {n} films so far</div>')
    elif room["phase"] == "voting":
        total = len(room["deck"])
        done = sum(1 for m in members if len(room["votes"].get(m, {})) >= total)
        md(f'<div class="sw-status"><span class="live"></span><b>{done} of {len(members)}</b> finished voting</div>')


ICON_STAR = ('<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M12 2.8l2.8 5.9 6.4.8-4.7 '
             '4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4-4.7-4.4 6.4-.8z"/></svg>')


def group_deck_html(room, todo, infos=None, region="GB", super_used=False):
    infos = infos or {}

    def card(k, cls):
        e = room["films"].get(k) or {"key": k, "name": k.split("|")[0], "year": "", "uri": "", "by": []}
        i = infos.get(k)
        return swipe_card({**e, "why": by_names(room, e), "meta": details_text(i), "stream": stream_text(i, region)},
                          cls)
    cards = card(todo[0], "top") + (card(todo[1], "next") if len(todo) > 1 else "")
    dis = " disabled" if super_used else ""
    hint = ("Super-like used. Right to watch, left to pass" if super_used
            else "Right to watch, left to pass. ★ is your one super-like (counts double)")
    return (f'<div class="sw-stack">{cards}</div><div class="sw-btns">'
            f'<button class="sw-btn sw-no" aria-label="Pass">{ICON_X}</button>'
            f'<button class="sw-btn sw-super" aria-label="Super-like"{dis}>{ICON_STAR}</button>'
            f'<button class="sw-btn sw-yes" aria-label="Watch">{ICON_HEART}</button></div>'
            f'<div class="sw-hint">{hint}</div>')


def group_film_picker(code, mid, room):
    """Let this person add their films: a list CSV, a full export (pick a list) or typed titles."""
    mine = room["members"][mid]["films"]
    if mine:
        items = "".join(f'<div class="reel-item">{poster_html(k, room["films"][k]["name"], room["films"][k]["year"], None)}</div>'
                        for k in mine if k in room["films"])
        section("Your films", kicker=f"{len(mine)} in the pot", first=True)
        md(f'<div class="reel">{items}</div>')
        with st.expander("Change my films"):
            group_film_form(code, mid)
    else:
        section("Add your films", kicker="Your picks", first=True,
                note="Upload your Letterboxd export and pick the list you made for tonight.")
        export_guide(group=True)
        group_film_form(code, mid)


def group_film_form(code, mid):
    films = []
    up = st.file_uploader("Your Letterboxd export", type=["zip", "csv"], key="gf_file", help=GROUP_UPLOAD_HELP)
    if up is not None:
        data = up.getvalue()
        if up.name.lower().endswith(".zip"):
            found = lists_in_export(data)
            if not found:
                st.caption("Couldn't find any lists in that export. Make a list on Letterboxd, then export again.")
            else:
                pick = st.selectbox("Which list?", list(found), key="gf_pick")
                films = found.get(pick, [])
        else:
            films = parse_list_csv(data)
            if not films:
                st.caption("That file doesn't look like a Letterboxd list. Try uploading your full export instead.")
    if films:
        extra = f" (first {GROUP_MAX_FILMS} used)" if len(films) > GROUP_MAX_FILMS else ""
        st.caption(f"{len(films)} films ready{extra}: " + ", ".join(f["name"] for f in films[:6])
                   + ("…" if len(films) > 6 else ""))
    st.button("Add to the pot", type="primary", width="stretch", disabled=not films,
              on_click=save_my_films, args=(code, mid, films), key="gf_save")


def group_view(code, mid):
    room = get_room(code)
    if not room or mid not in room["members"]:
        with header:
            marquee("Movie night", "Double <em>Feature</em>", "Group night")
        note("<b>That movie night has ended.</b> They last 12 hours. Start a new one from the home screen.")
        st.button("OK", on_click=leave_group)
        return
    me_ = room["members"][mid]
    is_host = mid == room["host"]
    host = room["members"][room["host"]]["name"]
    with header:
        marquee("Movie night", f"{esc(host)}'s <em>place</em>", f"Group night<i>✦</i>Room <b>{code}</b>")
        settings_menu()
    prefetch_posters([(k, f["name"], f["year"]) for k, f in room["films"].items()])
    seen = group_state(room)

    if room["phase"] == "lobby":
        if is_host:
            code_card(code, "Send this link to everyone, or have them enter the code in Double Feature "
                            "under Group night.", label="Room code")
        group_pulse(code, mid, seen)
        group_film_picker(code, mid, room)

    elif room["phase"] == "voting":
        group_pulse(code, mid, seen)
        mine = room["votes"].get(mid, {})
        todo = [k for k in room["deck"] if k not in mine]
        if todo:
            n = len(room["deck"])
            md(f'<div class="sw-head"><span>Voting as <b>{esc(me_["name"])}</b></span>'
               f'<span>{n - len(todo) + 1} / {n}</span></div>'
               f'<div class="sw-prog"><i style="width:{100 * (n - len(todo)) / max(n, 1):.1f}%"></i></div>')
            region = current_region()
            infos = film_infos([(k, room["films"][k]["name"], room["films"][k]["year"])
                                for k in room["deck"] if k in room["films"]])
            used = mid in room.get("supers", {})
            swiper(key=f"gdeck_{code}", data={"html": group_deck_html(room, todo, infos, region, used)},
                   on_swipe_change=lambda: group_vote(code, mid))
        else:
            note("<b>Your votes are in.</b> Results appear here as soon as everyone's finished.")
        if is_host:
            st.button("Close voting now", width="stretch", on_click=end_group_vote, args=(code,),
                      help="Count the votes so far, even if some people haven't finished.")

    else:  # results
        group_pulse(code, mid, seen)
        rows = tally(room)
        voters = sum(1 for v in room["votes"].values() if v)
        top_pts = rows[0][4] if rows else 0
        tied = [r[0] for r in rows if r[4] == top_pts] if top_pts else []
        win = room["films"].get(rows[0][0]) if rows else None
        if win and len(tied) == 1:
            _, w_yes, _, w_sup, _ = rows[0]
            sup_txt = f' · ★ {w_sup} super-like{"s" if w_sup != 1 else ""}' if w_sup else ""
            md(f'<div class="its-match"><div class="im-k">✦ The votes are in ✦</div>'
               f'<div class="im-t">Tonight\'s <em>pick</em></div>'
               f'<div class="im-n">{w_yes} of {voters} want to watch it{sup_txt}</div></div>')
            region = current_region()
            wi = film_infos([(win["key"], win["name"], win["year"])]).get(win["key"])
            md(ticket(win["key"], win["name"], win["year"], win["uri"],
                      esc(by_names(room, win)) + info_lines_html(wi, region), kicker="Group favourite",
                      admit=len(room["members"])))
            share_button(lambda: share_film_png("Group favourite", f"{host}'s movie night", win["key"], win["name"],
                                        win["year"], details_text(wi), stream_text(wi, region),
                                        real_poster_url(win["key"], "xl")),
                         "double-feature-movie-night.jpg", f"Tonight's movie night pick: {win['name']} 🍿",
                         key=f"share_group_{code}", label="Share the winner")
        elif tied:
            md(f'<div class="its-match"><div class="im-k">✦ The votes are in ✦</div>'
               f'<div class="im-t">It\'s a <em>tie</em></div>'
               f'<div class="im-n">{len(tied)} films are level at the top</div></div>')
        elif not voters:
            note("<b>Voting closed before anyone voted.</b>")
        else:
            note("<b>Nobody said yes to anything.</b> Maybe add some different films and try again.")

        shortlist = [r for r in rows if r[1]][:5]
        if shortlist and top_pts:
            section("The shortlist", kicker="Top films")
            bars = []
            for i, (k, y, n_, sup, _) in enumerate(shortlist, 1):
                e = room["films"][k]
                star = f'<span class="super-tag">★ {sup}</span>' if sup else ""
                pct = 100 * y / max(voters, 1)
                bars.append(f'<div class="row">{poster_html(k, e["name"], e["year"], e["uri"], "sm")}'
                            f'<div class="row-body"><div class="row-t"><a href="{esc(e["uri"])}" target="_blank">'
                            f'{esc(e["name"])}</a><span class="row-y">{esc(e["year"])}</span>{star}</div>'
                            f'<div class="gbar"><i style="width:{pct:.0f}%"></i></div>'
                            f'<div class="row-s">{esc(by_names(room, e))}</div></div>'
                            f'<div class="row-m"><div class="vs-gap" style="color:var(--gold)">{y}'
                            f'<small>of {voters}</small></div></div></div>')
            md("".join(bars))
        if is_host:
            runoff = tied if len(tied) > 1 else [r[0] for r in rows[:3] if r[1]]
            if len(runoff) > 1:
                st.button(f"Run-off: vote again on these {len(runoff)}", type="primary", width="stretch",
                          on_click=start_group_vote, args=(code, runoff))
            st.button("Back to the lobby to add films", width="stretch", on_click=back_to_lobby, args=(code,))
        else:
            st.caption(f"{host} can start a run-off or go back to add more films.")

    st.button("Leave movie night", on_click=leave_group, key="g_leave")
    attribution()


def group_join_screen(code):
    room = get_room(code)
    with header:
        if room:
            host = room["members"][room["host"]]["name"]
            marquee("Movie night", f"{esc(host)}'s <em>place</em>", f"Room <b>{code}</b>")
        else:
            marquee("Movie night", "Double <em>Feature</em>", "Group night")
    if not room:
        note("<b>That movie night has ended.</b> Ask for a new code.")
        st.button("Back", on_click=leave_group)
        return
    host = room["members"][room["host"]]["name"]
    note(f"<b>{esc(host)} is hosting a movie night.</b> Add your name, bring some films, then everyone votes.")
    st.text_input("Your name", key="gj_name", max_chars=24)
    st.button("Join", type="primary", width="stretch", on_click=join_group, args=(code,), key="gj_go")
    if st.session_state.get("gj_error"):
        st.caption(st.session_state.gj_error)
    others = [(k, m["name"]) for k, m in room["members"].items()]
    if others:
        with st.expander("Already joined on another phone?"):
            cols = st.columns(2)
            for i, (k, n) in enumerate(others):
                cols[i % 2].button(n, key=f"grejoin_{k}", width="stretch", on_click=rejoin_group, args=(code, k))
    st.button("Cancel", on_click=leave_group, key="gj_cancel")


GROUP_CSS = """
<style>
.gms { display: flex; flex-wrap: wrap; gap: 6px; margin: .45rem 0 .9rem; }
.gm { display: flex; align-items: baseline; gap: .45rem; padding: .4rem .75rem .35rem; border-radius: 999px;
  background: rgba(27,37,54,.85); box-shadow: inset 0 0 0 1px var(--line); font-size: .88rem; }
.gm-n { color: var(--cream); font-weight: 600; }
.gm-c { color: var(--muted); font-size: .78rem; }
.gbar { height: 5px; border-radius: 5px; background: rgba(243,236,221,.08); overflow: hidden; margin-top: .4rem; }
.gbar i { display: block; height: 100%; background: linear-gradient(90deg, var(--gold), var(--sea)); }
.reel-item .poster { width: 100%; }
.tap-card { border-radius: 14px; padding: .65rem .8rem; background: rgba(27,37,54,.8); box-shadow: inset 0 0 0 1px var(--line);
  border-bottom: 0; margin-top: -.3rem; }
[data-testid="stElementToolbar"] { display: none !important; }  /* chart toolbar covers text on phones */
/* Touch screens: no hover tooltip (it glitches on tap), the card under the chart does the job instead */
@media (hover: none) { #vg-tooltip-element { display: none !important; } }
@media (hover: hover) { .tap-hint { display: none; } }
.tap-hint { text-align: center; color: var(--muted); font-size: .82rem; margin-top: -.4rem; }
</style>
"""
md(GROUP_CSS)


header = st.container()
have_both = all(st.session_state.get(k) for k in ("file_a", "file_b"))

# A shared link (?room=CODE) works once per page load, for pair codes and swipe codes alike
qp = (st.query_params.get("room") or "").strip().upper()
if qp and st.session_state.get("qp_handled") != qp:
    st.session_state.qp_handled = qp
    if not (st.session_state.get("pair") or st.session_state.get("group")):
        st.session_state.join_error = route_code(qp)

# Group movie night takes over the whole screen
if st.session_state.get("group"):
    group_view(st.session_state.group, st.session_state.get("gid"))
    st.stop()
if st.session_state.get("group_join"):
    group_join_screen(st.session_state.group_join)
    st.stop()

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
        note(f"<b>{esc(room['slots'][0]['name'])} wants to pair up.</b> Add your Letterboxd export to join.")
        export_guide()
        st.text_input("Your name", key="pj_name", placeholder=NAME_HINT, help=NAME_HELP)
        st.file_uploader("Your Letterboxd export", type=["zip", "csv"], accept_multiple_files=True, key="pj_file",
                         help=UPLOAD_HELP)
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
        settings_menu()
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
        "How are you doing this?", ["own", "one", "group"], default="own", key="mode",
        label_visibility="collapsed",
        format_func={"own": "Two phones", "one": "One phone", "group": "Group night"}.get) or "own"
    if not have_both:
        with header:
            marquee("Now showing", "Double <em>Feature</em>",
                    "A whole group<i>✦</i>One movie night" if mode == "group"
                    else "Two Letterboxd accounts<i>✦</i>One movie night")
            how_it_works(pairing=mode == "own", group=mode == "group")

    if mode == "group":
        st.text_input("Your name", key="gh_name", max_chars=24, placeholder="So your friends know who's hosting")
        st.button("Host a movie night", type="primary", width="stretch", on_click=host_group)
        join_box()
        st.stop()

    if mode == "own":
        export_guide()
        st.text_input("Your name", key="sp_name", placeholder=NAME_HINT, help=NAME_HELP)
        st.file_uploader("Your Letterboxd export", type=["zip", "csv"], accept_multiple_files=True, key="sp_file",
                         help=UPLOAD_HELP)
        st.button("Get a pair code", type="primary", width="stretch", on_click=start_pair,
                  disabled=not st.session_state.get("sp_file"))
        if st.session_state.get("sp_error"):
            st.error(st.session_state.sp_error)
        join_box()
        st.stop()

    if not have_both:
        export_guide()
    with st.expander("Your Letterboxd exports", expanded=not have_both):
        name_a = st.text_input("First person's name", placeholder=NAME_HINT, help=NAME_HELP)
        file_a = st.file_uploader("First person's export", type=["zip", "csv"],
                                  accept_multiple_files=True, key="file_a", help=UPLOAD_HELP)
        st.divider()
        name_b = st.text_input("Second person's name", placeholder=NAME_HINT, help=NAME_HELP)
        file_b = st.file_uploader("Second person's export", type=["zip", "csv"],
                                  accept_multiple_files=True, key="file_b", help=UPLOAD_HELP)
    if not (file_a and file_b):
        join_box()
        st.stop()
    raw_a, raw_b = bundle(file_a), bundle(file_b)

def safe_load(raw):
    try:
        return load_export(raw) if raw else None
    except (zipfile.BadZipFile, pd.errors.ParserError, UnicodeDecodeError):
        return None


da, db = safe_load(raw_a), safe_load(raw_b)
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
    settings_menu()

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

    # Film details: length, genre and where it's streaming (only when a TMDB key is set up)
    region, infos = current_region(), {}
    if tmdb_on() and pool:
        with st.spinner("Fetching film details…"):
            infos = film_infos([(k, str(cat.at[k, "Name"]), cat.at[k, "Year"]) for k in pool])
        remember_posters(infos)
        lengths = {"any": "Any length", "90": "Under 1½h", "120": "Under 2h", "150": "Under 2½h"}
        max_len = filters.segmented_control("Length", list(lengths), default="any", key="f_len",
                                            format_func=lengths.get) or "any"
        genres = sorted({g for i in infos.values() for g in i.get("genres", [])})
        want_g = filters.pills("Genre", genres, selection_mode="multi", key="f_genre") if genres else []
        svcs = sorted({x for i in infos.values() for x in services(i, region)})
        want_s = filters.pills("Streaming on", svcs, selection_mode="multi", key="f_svc") if svcs else []

        def keep(k):
            i = infos.get(k) or {}
            if max_len != "any" and i.get("runtime") and i["runtime"] > int(max_len):
                return False
            if want_g and not set(want_g) & set(i.get("genres", [])):
                return False
            return not want_s or bool(set(want_s) & set(services(i, region)))

        pool = {k: why for k, why in pool.items() if keep(k)}

    def pool_cap(k):
        bits = [reason_text(pool[k])] if pool[k] else []
        i = infos.get(k)
        if i:
            sv = services(i, region)
            extra = " · ".join(x for x in (fmt_runtime(i.get("runtime")), sv[0] if sv else "") if x)
            if extra:
                bits.append(esc(extra))
        return "<br>".join(bits)

    if not pool:
        note("<b>Nothing in the pool.</b> Open Filters to add films one of you loved, or clear the decades.")
    else:
        pick = st.session_state.get("pick")
        if pick in pool:
            r = cat.loc[pick]
            flip = "a" if st.session_state.get("picks", 0) % 2 else "b"
            md(ticket(pick, r["Name"], year_str(r["Year"]), r["Letterboxd URI"],
                      reason_text(pool[pick], on_ticket=True) + info_lines_html(infos.get(pick), region), flip=flip))
            share_button(lambda: share_film_png("Tonight's pick", f"{A} & {B}", pick, str(r["Name"]), year_str(r["Year"]),
                                        details_text(infos.get(pick)), stream_text(infos.get(pick), region),
                                        real_poster_url(pick, "xl")),
                         "double-feature-pick.jpg", f"Tonight's pick: {r['Name']} 🎬", key="share_pick",
                         label="Share tonight's pick")
        else:
            md(f'<div class="tk-ghost"><b>What are we watching?</b>{len(pool)} films in the hat. '
               f'Let fate decide.</div>')
        st.button("Pick another" if pick in pool else "Pick tonight's film", type="primary",
                  width="stretch", on_click=pick_film, args=(sorted(pool),))

        section("The pool", kicker=f"{len(pool)} films in the hat")
        poster_wall(sorted(pool, key=lambda k: str(cat.at[k, "Name"]).casefold()), key="all_pool", cap=pool_cap)


# ---------- Swipe ----------

def start_room(names, deck):
    st.session_state.room = create_room(names, deck)


with t_swipe:
    code = st.session_state.get("room")
    sc = pair["swipe"] if paired else None
    deck = [{"key": k, "name": str(cat.at[k, "Name"]), "year": year_str(cat.at[k, "Year"]),
             "uri": str(cat.at[k, "Letterboxd URI"]) if pd.notna(cat.at[k, "Letterboxd URI"]) else "",
             "why": reason_plain(why), "meta": details_text(infos.get(k)),
             "stream": stream_text(infos.get(k), region)} for k, why in pool.items()]
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
        share_button(lambda: share_taste_png(score, verdict, A, B, len(common), round(float(gap.mean()), 1), tougher),
                     "double-feature-taste.jpg", f"Our taste match: {score}% 🎬", key="share_taste",
                     label="Share our taste match")

        section("Side by side", kicker="Every shared rating",
                note=f'Each dot is a film. <span style="color:{GOLD}">Gold</span>: {esc(A)} rated it higher. '
                     f'<span style="color:{SEA}">Green</span>: {esc(B)} did. On the line: you agreed.')
        rng = random.Random(7)
        plot = pd.DataFrame({"k": list(common), "Film": cat.loc[common, "Name"].values,
                             "a": ra.values, "b": rb.values})
        plot["aj"] = plot["a"] + [rng.uniform(-0.13, 0.13) for _ in range(len(plot))]
        plot["bj"] = plot["b"] + [rng.uniform(-0.13, 0.13) for _ in range(len(plot))]
        plot["Higher"] = ["Agreed" if x == y else (A if x > y else B) for x, y in zip(plot["a"], plot["b"])]
        scale = alt.Scale(domain=[0.25, 5.25], nice=False)
        ax = dict(values=[1, 2, 3, 4, 5], labelExpr="datum.value + '★'")
        # Tapping (or clicking) picks the nearest dot, so it works on phones where there's no hover
        tap = alt.selection_point(name="tap", fields=["k"], on="click", nearest=True, clear="dblclick")
        points = alt.Chart(plot).mark_circle(stroke="#111926", strokeWidth=1).encode(
            x=alt.X("aj:Q", title=f"{A} →", scale=scale, axis=alt.Axis(**ax)),
            y=alt.Y("bj:Q", title=f"{B} →", scale=scale, axis=alt.Axis(**ax)),
            color=alt.Color("Higher:N", scale=alt.Scale(domain=[A, B, "Agreed"], range=[GOLD, SEA, CREAM]),
                            legend=None),
            size=alt.condition(tap, alt.value(260), alt.value(110)),
            opacity=alt.value(0.85),
            strokeWidth=alt.condition(tap, alt.value(3), alt.value(1)),
            stroke=alt.condition(tap, alt.value(CREAM), alt.value("#111926")),
            tooltip=["Film", alt.Tooltip("a:Q", title=A), alt.Tooltip("b:Q", title=B)],
        ).add_params(tap)
        diagonal = alt.Chart(pd.DataFrame({"aj": [0.25, 5.25], "bj": [0.25, 5.25]})).mark_line(
            strokeDash=[3, 5], color="#5C6A82", strokeWidth=1.5).encode(x="aj:Q", y="bj:Q")
        chart = (diagonal + points).properties(height=330).configure(
            background="transparent", font="DM Sans").configure_view(stroke=None).configure_axis(
            labelColor=MUTED, titleColor=MUTED, gridColor="#ffffff12", domain=False, ticks=False,
            labelFontSize=12, titleFontSize=12, titleFontWeight=500, labelPadding=8, titlePadding=10)
        event = st.altair_chart(chart, theme=None, on_select="rerun", selection_mode="tap", key="taste_scatter")
        prefetch_posters([(k, str(cat.at[k, "Name"]), cat.at[k, "Year"]) for k in common
                          if (ra[k] >= 4.5 and rb[k] >= 4.5) or abs(ra[k] - rb[k]) >= 1.5])
        picked = [p.get("k") for p in (event.selection.get("tap") or []) if isinstance(p, dict)] if event else []
        k = picked[0] if picked and picked[0] in ra.index else None
        if k:
            prefetch_posters([(k, str(cat.at[k, "Name"]), cat.at[k, "Year"])])
            md(f'<div class="row tap-card">{poster(k, "sm")}<div class="row-body"><div class="row-t">{title_link(k)}'
               f'<span class="row-y">{year_str(cat.at[k, "Year"])}</span></div>'
               f'<div class="row-s">{rating_line(A, ra[k])}{rating_line(B, rb[k])}</div></div></div>')
        else:
            md('<div class="tap-hint">Tap a dot to see which film it is</div>')

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
        prefetch_posters([(k, str(cat.at[k, "Name"]), cat.at[k, "Year"]) for k in order[:60]])
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

attribution()
