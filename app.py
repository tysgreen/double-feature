"""Double Feature: compare two Letterboxd accounts and pick a film together."""

import base64
import concurrent.futures
import copy
import html
import io
import math
import os
import random
import re
import secrets
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
from PIL import Image, ImageDraw, ImageFont

st.set_page_config(page_title="Double Feature", page_icon="🎟️", layout="centered",
                   initial_sidebar_state="collapsed")

EMPTY = pd.DataFrame(columns=["Date", "Name", "Year", "Letterboxd URI"])


# ---------- Loading a Letterboxd export ----------

def _csv_name(path: str) -> str:
    """'Ratings (1).csv' -> 'ratings.csv', so re-downloaded or renamed copies still count."""
    base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return re.sub(r"\s*\(\d+\)(?=\.csv$)", "", base)


def _find(z: zipfile.ZipFile, filename: str):
    """Find a CSV in the export, skipping the deleted/orphaned/likes/lists folders and Mac junk."""
    skip = {"deleted", "orphaned", "likes", "lists", "__macosx"}
    for n in z.namelist():
        parts = n.lower().replace("\\", "/").split("/")
        if _csv_name(n) == filename and not skip.intersection(parts[:-1]) and not parts[-1].startswith("._"):
            return n
    return None


def _read_csv(data: bytes) -> pd.DataFrame:
    """Read a CSV as plain text. Copes with a BOM, Excel's semicolons and non-UTF-8 files."""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        first = text.split("\n", 1)[0]
        seps = [";", ","] if first.count(";") > first.count(",") else [",", ";"]  # Excel in some countries uses ;
        for sep in seps:
            try:
                df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=sep)
            except pd.errors.EmptyDataError:
                return pd.DataFrame()
            except pd.errors.ParserError:
                continue
            if df.shape[1] > 1 or sep == seps[-1]:
                return df
        return pd.DataFrame()
    return pd.DataFrame()


def safe_uri(u):
    """Only real web links are kept, so nothing odd from an uploaded file ends up in a link."""
    u = "" if u is None or (not isinstance(u, str) and pd.isna(u)) else str(u).strip()
    return u if u.startswith(("https://", "http://")) else None


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("Name", "Year", "Letterboxd URI"):
        if col not in df.columns:
            df[col] = ""
    df["Name"] = df["Name"].fillna("").astype(str).str.strip()
    df = df[df["Name"] != ""].copy()
    year = pd.to_numeric(df["Year"], errors="coerce")
    year = year.where((year == year.round()) & year.between(1870, 2100))
    df["Year"] = year.astype("Int64")
    df["Letterboxd URI"] = df["Letterboxd URI"].map(safe_uri)
    # Match films across accounts on title + year ("<NA>" when a film has no year yet)
    yr = df["Year"].map(lambda y: "<NA>" if pd.isna(y) else str(int(y))).astype(str)
    df["key"] = df["Name"].str.casefold() + "|" + yr
    return df.drop_duplicates("key", keep="last")


# Parsed exports are cached so tab switches are instant, but capped so a busy day can't fill memory
@st.cache_data(show_spinner=False, max_entries=100, ttl=12 * 3600)
def load_export(raw: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(raw))
    out = {"found": 0}
    for name in ("watched", "ratings", "watchlist"):
        path = _find(z, f"{name}.csv")
        out["found"] += bool(path)
        out[name] = _clean(_read_csv(z.read(path)) if path else EMPTY)
    if "Rating" not in out["ratings"].columns:
        out["ratings"]["Rating"] = pd.Series(dtype=float)
    # profile.csv gives a default display name: given name, else Letterboxd username
    out["name"] = ""
    path = _find(z, "profile.csv")
    if path:
        prof = _read_csv(z.read(path))
        if len(prof):
            row = prof.iloc[0]
            out["name"] = clean_name(row.get("Given Name", "") or row.get("Username", ""))
    return out


def summarise(d: dict) -> dict:
    seen = set(d["watched"]["key"]) | set(d["ratings"]["key"])
    ratings = pd.to_numeric(d["ratings"].set_index("key")["Rating"].astype(str).str.replace(",", ".", regex=False),
                            errors="coerce").dropna()
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

CSS = """
<style>
:root {
  --sans: "Bricolage Grotesque", system-ui, sans-serif;
  --display: var(--sans); --label: var(--sans);
  --stars: "DejaVu Sans", "Segoe UI Symbol", "Apple Symbols", sans-serif;
}
@property --p { syntax: "<number>"; inherits: true; initial-value: 0; }

/* Page: a soft background the person picks in Settings (or After dark) */
.stApp { background: var(--bg); color: var(--text); font-family: var(--sans); }
[data-testid="stHeader"] { background: transparent; }
/* The top padding leaves room for the hosting toolbar (Streamlit Cloud puts its icons up there) */
[data-testid="stMainBlockContainer"], .block-container { max-width: 560px; padding: 3.4rem 1rem 6rem; }
[data-testid="stMarkdownContainer"] { color: var(--text); }
[data-testid="stMarkdownContainer"] p { margin-bottom: 0; }
a { -webkit-tap-highlight-color: transparent; }
.st-key-prefs { display: none !important; }
/* The CSS blocks render as empty markdown elements; drop them so they don't add gaps above the header */
[data-testid="stElementContainer"]:has(style) { display: none !important; }

/* ---------- Header ---------- */
.st-key-hdr { position: relative; }
.st-key-settings_wrap { position: absolute !important; top: .15rem; right: 0; width: auto !important; z-index: 5; }
.st-key-settings_wrap [data-testid="stPopoverButton"], .st-key-settings_wrap button {
  min-height: 2.5rem; border-radius: 999px !important; border: 2px solid var(--line) !important;
  background: var(--surface) !important; color: var(--text) !important; padding: 0 .85rem; }
.st-key-settings_wrap button p { font-weight: 700; font-size: .95rem; }
.marquee { margin: .35rem 0 1.1rem; }
.mq-brand { font-weight: 800; font-size: 1.15rem; letter-spacing: -.02em; line-height: 2.5rem; }
.mq-brand span { color: var(--accent-ink); }
.mq-kick { margin-top: 1rem; font-weight: 700; letter-spacing: .14em; font-size: .78rem; color: var(--muted);
  text-transform: uppercase; }
.mq-title { font-weight: 800; font-size: 2.75rem; line-height: .95; letter-spacing: -.045em; margin: .3rem 0 .55rem;
  overflow-wrap: anywhere; }
.mq-title em { font-style: normal; color: var(--accent-ink); }
.mq-sub { font-size: .98rem; font-weight: 500; color: var(--muted); }
.mq-sub b { color: var(--text); font-weight: 800; }
.mq-sub i { font-style: normal; color: var(--accent-ink); padding: 0 .45rem; }

/* ---------- Tabs: a dark pill bar, the open tab in butter ---------- */
[data-testid="stTabs"] [role="tablist"] {
  gap: 2px; padding: 5px; border-radius: 999px; background: var(--nav-bg); border: 2px solid var(--line);
  margin-bottom: .7rem; overflow: visible;
}
[data-testid="stTabs"] [role="tab"] {
  flex: 1 1 0; justify-content: center; border-radius: 999px; padding: .5rem 0 .45rem; margin: 0;
  height: auto; border: 0 !important; background: transparent; transition: background .2s, color .2s;
}
[data-testid="stTabs"] [role="tab"] p { font-size: .95rem !important; font-weight: 700; color: var(--nav-fg); }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { background: var(--butter); }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { color: #1F1A17; font-weight: 800; }
[data-testid="stTabs"] [role="tablist"]::after, [data-testid="stTabs"] [role="tablist"]::before,
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"], [data-testid="stTabs"] .react-aria-SelectionIndicator { display: none !important; }

/* ---------- Streamlit widgets, restyled as stickers ---------- */
[data-testid="stExpander"] details { border-radius: 18px; border: 2px solid var(--line); background: var(--surface);
  box-shadow: 3px 3px 0 var(--line); }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p { color: var(--text); font-weight: 700; }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary:hover { background: transparent !important; border-radius: 16px; }
[data-testid="stExpander"] summary svg { color: var(--text); fill: var(--text); }
[data-testid="stBaseButton-primary"] {
  min-height: 3.5rem; border: 2px solid var(--line); background: var(--cta-bg); color: var(--cta-fg);
  box-shadow: none; }
[data-testid="stBaseButton-primary"] p { font-size: 1.15rem !important; font-weight: 800; color: var(--cta-fg); }
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primary"]:focus-visible {
  background: var(--cta-bg); border-color: var(--line); color: var(--cta-fg); filter: brightness(1.12); }
[data-testid="stBaseButton-primary"]:disabled { opacity: 1; background: var(--surface); border: 2px dashed var(--muted);
  filter: none; cursor: not-allowed; }
[data-testid="stBaseButton-primary"]:disabled p { color: var(--muted); }
[data-testid="stBaseButton-primary"]:active { transform: translateY(1px) scale(.995); }
[data-testid="stBaseButton-secondary"], [data-testid="stBaseLinkButton-secondary"], [data-testid="stPopoverButton"] {
  min-height: 2.9rem; border: 2px solid var(--line); background: var(--surface); color: var(--text); }
[data-testid="stBaseButton-secondary"] p, [data-testid="stBaseLinkButton-secondary"] p, [data-testid="stPopoverButton"] p {
  font-weight: 700; color: var(--text); }
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stBaseLinkButton-secondary"]:hover {
  border-color: var(--line); color: var(--text); background: var(--surface); filter: brightness(.97); }
[data-testid="stBaseButton-tertiary"] p { color: var(--text); font-weight: 700; }
button[data-variant="pills"], button[data-variant="segmented_control"] {
  min-height: 2.4rem; border: 2px solid var(--line) !important; background: var(--surface) !important;
  color: var(--text) !important; font-weight: 700; }
button[data-variant="pills"] p, button[data-variant="segmented_control"] p { color: var(--text) !important; font-weight: 700; }
button[data-variant="pills"][data-selected="true"], button[data-variant="segmented_control"][data-selected="true"],
button[data-variant="pills"][aria-checked="true"], button[data-variant="segmented_control"][aria-checked="true"] {
  background: var(--butter) !important; color: #1F1A17 !important; }
button[data-variant="pills"][data-selected="true"] p, button[data-variant="segmented_control"][data-selected="true"] p,
button[data-variant="pills"][aria-checked="true"] p, button[data-variant="segmented_control"][aria-checked="true"] p {
  color: #1F1A17 !important; font-weight: 800; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label { color: var(--text) !important; font-weight: 700; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--muted) !important; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div, [data-baseweb="textarea"],
[data-testid="stTextInput"] div:has(> input), [data-testid="stTextArea"] div:has(> textarea),
[data-testid="stSelectbox"] [role="group"] {
  background: var(--surface) !important; border-color: var(--line) !important; border-width: 2px !important; }
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, [data-baseweb="select"] div,
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea, [data-testid="stSelectbox"] input {
  background: transparent !important; color: var(--text) !important; -webkit-text-fill-color: var(--text); }
[data-testid="stSelectbox"] button svg { color: var(--text); fill: var(--text); }
[role="listbox"], [data-testid="stSelectboxVirtualDropdown"] { background: var(--surface) !important; }
[role="listbox"] [role="option"], [role="listbox"] [role="option"] * { color: var(--text) !important; }
input::placeholder, textarea::placeholder { color: var(--muted) !important; -webkit-text-fill-color: var(--muted); opacity: 1; }
[data-baseweb="select"] svg { color: var(--text); }
[data-baseweb="popover"] ul, [data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"] { background: var(--surface) !important; }
[data-baseweb="popover"] li { color: var(--text) !important; }
[data-testid="stPopoverBody"] { background: var(--bg) !important; border: 2px solid var(--line); border-radius: 18px;
  box-shadow: 4px 4px 0 var(--line); color: var(--text); }
[data-testid="stFileUploaderDropzone"] { border: 2.5px dashed var(--line); background: color-mix(in srgb, var(--surface) 60%, transparent);
  border-radius: 18px; }
[data-testid="stFileUploaderDropzone"] *, [data-testid="stFileUploaderFile"] * { color: var(--text) !important; }
[data-testid="stFileUploaderDropzone"] button { border: 2px solid var(--line); background: var(--surface); }
[data-testid="stCode"] pre, [data-testid="stCode"] code { background: var(--surface) !important; color: var(--text) !important; }
[data-testid="stCode"] { border: 2px solid var(--line); border-radius: 14px; overflow: hidden; }
[data-testid="stAlertContainer"] { border: 2px solid var(--line); border-radius: 16px; }
[data-testid="stToast"] { background: var(--surface) !important; color: var(--text) !important; border: 2px solid var(--line); }
[data-testid="stCheckbox"] label p, [data-testid="stToggle"] label p { color: var(--text) !important; }
[data-testid="stTabPanel"] > div > div:first-child .sec, [data-testid="stTabPanel"] .sec.first { margin-top: .6rem; }
[data-testid="stSpinner"] * { color: var(--muted) !important; }

/* Colour swatches on the background picker */
.st-key-theme_pick button { gap: .35rem; padding: 0 .55rem; }
.st-key-theme_pick button:before { content: ""; width: .9rem; height: .9rem; flex: none;
  border-radius: 50%; border: 2px solid var(--text); background: var(--sw); }
.st-key-theme_pick button:nth-of-type(1) { --sw: #ECE7F7; } .st-key-theme_pick button:nth-of-type(2) { --sw: #FBF1D3; }
.st-key-theme_pick button:nth-of-type(3) { --sw: #F9E7E8; } .st-key-theme_pick button:nth-of-type(4) { --sw: #DCE3FA; }
.st-key-theme_pick button:nth-of-type(5) { --sw: #1D1828; }

/* ---------- Section headings ---------- */
.sec { margin: 1.9rem 0 .8rem; }
.sec-k { font-weight: 700; letter-spacing: .14em; font-size: .78rem; color: var(--muted); text-transform: uppercase; }
.sec-t { font-weight: 800; font-size: 1.75rem; line-height: 1.02; letter-spacing: -.035em; margin: .2rem 0 0; }
.sec-n { color: var(--muted); font-size: .95rem; font-weight: 500; margin-top: .4rem; line-height: 1.4; }
.note { border-radius: 18px; padding: 1rem 1.1rem; background: var(--surface); border: 2px solid var(--line);
  color: var(--muted); line-height: 1.45; font-size: .97rem; font-weight: 500; }
.note b { color: var(--text); font-weight: 800; }

/* ---------- Posters ---------- */
.poster { position: relative; display: flex; flex-direction: column; aspect-ratio: 2 / 3; overflow: hidden;
  border-radius: 10px; color: var(--pi); text-decoration: none !important; isolation: isolate;
  border: 2px solid var(--line); box-shadow: 3px 3px 0 var(--line); box-sizing: border-box; }
.poster, .poster:link, .poster:visited, .poster:hover { color: var(--pi) !important; }
.p-t { font-weight: 800; line-height: 1.02; padding: 9% 9% 0; letter-spacing: -.02em;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
.p-y { font-weight: 700; letter-spacing: .1em; margin-top: auto; padding: 0 9% 7%; opacity: .9; }
.p-sm { width: 44px; flex: 0 0 44px; border-radius: 7px; border-width: 1.5px; box-shadow: none; }
.poster.real .p-img { position: absolute; inset: 0; z-index: 1; background: center / cover no-repeat; }
.sw-title { font-size: 1.25rem; font-weight: 800; line-height: 1.1; letter-spacing: -.02em; margin-top: .1rem; }
.p-sm .p-t, .p-sm .p-y { display: none; }
.p-md .p-t { font-size: .82rem; } .p-md .p-y { font-size: .7rem; }
.p-lg { width: 92px; flex: 0 0 92px; transform: rotate(-4deg); } .p-lg .p-t { font-size: .8rem; } .p-lg .p-y { font-size: .68rem; }

.wall { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px 12px; }
.wall-item { min-width: 0; }
.wall-cap { font-size: .76rem; font-weight: 600; color: var(--muted); margin-top: .5rem; line-height: 1.3; }
.reel { display: flex; gap: 14px; overflow-x: auto; scroll-snap-type: x mandatory; padding: 4px 4px 12px;
  margin: 0 -1rem; padding-left: 1rem; scrollbar-width: none; }
.reel::-webkit-scrollbar { display: none; }
.reel-item { flex: 0 0 116px; scroll-snap-align: start; }
.reel-item:last-child { margin-right: 1rem; }
.reel-cap { margin-top: .55rem; font-size: .8rem; line-height: 1.5; }

/* ---------- Film rows: each one a little card ---------- */
.row { display: flex; align-items: center; gap: .85rem; padding: .6rem .75rem; margin-bottom: 8px;
  background: var(--surface); border: 2px solid var(--line); border-radius: 16px; }
.row-body { flex: 1; min-width: 0; }
.row-t { font-size: 1.05rem; line-height: 1.15; font-weight: 800; letter-spacing: -.01em; }
.row-t a { color: var(--text) !important; text-decoration: none; }
.row-y { font-weight: 600; color: var(--muted); font-size: .85rem; margin-left: .35rem; }
.row-s { font-size: .84rem; color: var(--muted); margin-top: .25rem; line-height: 1.5; font-weight: 500; }
.row-m { text-align: right; white-space: nowrap; }
.chip { display: inline-block; font-weight: 700; font-size: .8rem; padding: .15rem .6rem; border-radius: 999px;
  background: var(--surface); border: 1.5px solid var(--line); color: var(--text); }
.chip-gold { background: var(--butter); color: #1F1A17; }

/* Stars: five glyphs, filled to the rating, so half stars read like Letterboxd */
.st { font-family: var(--stars); letter-spacing: .04em; white-space: nowrap;
  background: linear-gradient(90deg, var(--text) calc(var(--r) * 20%), var(--track) 0);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.dot { display: inline-block; width: .6rem; height: .6rem; border-radius: 50%; margin-right: .4rem;
  vertical-align: .02rem; border: 1.5px solid var(--line); box-sizing: border-box; }
.rl { display: flex; align-items: center; gap: .4rem; }
.rl .nm { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); font-weight: 600; }

/* ---------- Ticket: tonight's pick ---------- */
.ticket {
  --notch: 64px; display: flex; color: #1F1A17; margin: .4rem 0 1.1rem; border-radius: 20px; background: var(--butter);
  border: 2px solid var(--line); box-shadow: 4px 4px 0 var(--line);
}
.ticket.a { animation: deal-a .55s cubic-bezier(.2,.9,.25,1.15) both; }
.ticket.b { animation: deal-b .55s cubic-bezier(.2,.9,.25,1.15) both; }
@keyframes deal-a { from { opacity: 0; transform: translateY(-18px) rotate(-2.5deg) scale(.96); } }
@keyframes deal-b { from { opacity: 0; transform: translateY(-18px) rotate(2.5deg) scale(.96); } }
.tk-stub { flex: 0 0 var(--notch); display: flex; flex-direction: column; align-items: center; justify-content: center;
  border-right: 2px dashed rgba(31,26,23,.45); padding: 1rem 0; }
.tk-stub span { writing-mode: vertical-rl; transform: rotate(180deg); font-weight: 800; text-transform: uppercase;
  letter-spacing: .22em; font-size: 1.05rem; line-height: 1; }
.tk-stub small { writing-mode: vertical-rl; transform: rotate(180deg); font-weight: 700;
  letter-spacing: .14em; font-size: .68rem; opacity: .6; margin-top: .8rem; }
.tk-main { flex: 1; min-width: 0; padding: 1rem 1rem 1rem .95rem; }
.tk-kick { font-weight: 800; letter-spacing: .16em; font-size: .72rem; text-transform: uppercase; opacity: .75; }
.tk-body { display: flex; gap: .9rem; margin-top: .6rem; align-items: flex-start; }
.tk-body .poster { --line: #1F1A17; }
.tk-title { font-weight: 800; font-size: 1.6rem; line-height: 1; letter-spacing: -.035em; overflow-wrap: anywhere; }
.tk-year { font-weight: 700; letter-spacing: .08em; font-size: .95rem; margin-top: .35rem; opacity: .75; }
.tk-why { font-size: .84rem; font-weight: 500; margin-top: .55rem; line-height: 1.35; }
.tk-why .st { background: linear-gradient(90deg, #1F1A17 calc(var(--r) * 20%), rgba(31,26,23,.2) 0);
  -webkit-background-clip: text; background-clip: text; }
.tk-link { display: inline-flex; align-items: center; min-height: 2.5rem; margin-top: .9rem; font-weight: 800; font-size: .9rem;
  padding: 0 1rem; border-radius: 12px; background: #1F1A17; color: #F6D776 !important; text-decoration: none !important; }
.tk-ghost { border-radius: 20px; padding: 1.4rem 1.2rem; text-align: center; margin: .4rem 0 1.1rem;
  border: 2.5px dashed var(--line); color: var(--muted); font-weight: 500; }
.tk-ghost b { display: block; font-size: 1.35rem; color: var(--text); font-weight: 800; margin-bottom: .25rem; letter-spacing: -.02em; }

/* ---------- Taste ---------- */
.match { display: flex; flex-direction: column; align-items: center; text-align: center; margin: .6rem 0 .4rem; }
.ring { display: grid; place-items: center; width: 190px; aspect-ratio: 1; position: relative; border-radius: 50%;
  border: 2px solid var(--line); box-shadow: 5px 5px 0 var(--line); background: var(--surface);
  animation: fill 1.4s cubic-bezier(.2,.8,.2,1) both; }
@keyframes fill { from { --p: 0; } }
.ring:before { content: ""; position: absolute; inset: 0; border-radius: 50%;
  background: conic-gradient(var(--pink) calc(var(--p) * 1%), var(--surface) 0);
  -webkit-mask: radial-gradient(farthest-side, #0000 calc(100% - 26px), #000 calc(100% - 25px));
          mask: radial-gradient(farthest-side, #0000 calc(100% - 26px), #000 calc(100% - 25px)); }
.ring:after { content: ""; position: absolute; inset: 24px; border-radius: 50%; border: 2px solid var(--line); }
.ring > * { position: relative; z-index: 1; }
.ring b { font-size: 3.1rem; font-weight: 800; line-height: 1; letter-spacing: -.05em; }
.ring b small { font-size: 1.5rem; margin-left: .02em; }
.ring span { display: block; font-weight: 700; letter-spacing: .14em; font-size: .7rem; color: var(--muted);
  margin-top: .25rem; text-transform: uppercase; }
.verdict { font-size: 1.9rem; font-weight: 800; letter-spacing: -.04em; margin-top: 1.1rem; color: var(--text); line-height: 1; }
.verdict-n { color: var(--muted); font-size: .95rem; font-weight: 500; margin-top: .4rem; }
.trio { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 1.2rem 0 .2rem; }
.trio div { border-radius: 16px; background: var(--surface); border: 2px solid var(--line);
  padding: .8rem .5rem .7rem; text-align: center; }
.trio div:nth-child(1) { background: var(--butter); color: #1F1A17; }
.trio div:nth-child(1) span { color: #1F1A17; opacity: .75; }
.trio b { display: block; font-size: 1.5rem; font-weight: 800; line-height: 1.1; letter-spacing: -.03em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trio span { font-weight: 700; letter-spacing: .06em; font-size: .72rem; color: var(--muted); text-transform: uppercase; }
.vs-gap { font-size: 1.35rem; font-weight: 800; color: var(--accent-ink); line-height: 1; }
.vs-gap small { display: block; font-weight: 700; letter-spacing: .06em; font-size: .68rem;
  color: var(--muted); margin-top: .2rem; text-transform: uppercase; }

/* ---------- Stats: tale of the tape ---------- */
.tape { border-radius: 20px; background: var(--surface); border: 2px solid var(--line); box-shadow: 4px 4px 0 var(--line);
  padding: .4rem 1rem .7rem; }
.tape-h { display: flex; justify-content: space-between; align-items: center; padding: .7rem 0 .6rem;
  border-bottom: 2px solid var(--line); font-size: 1.2rem; font-weight: 800; }
.tape-h i { font-style: normal; font-weight: 700; color: var(--muted); font-size: .9rem; }
.tape-r { display: grid; grid-template-columns: 1fr auto 1fr; align-items: baseline; padding: .75rem 0 .25rem; }
.tape-r b { font-weight: 800; font-size: 1.35rem; letter-spacing: -.02em; }
.tape-r b:last-of-type { text-align: right; }
.tape-r span { font-weight: 700; letter-spacing: .06em; font-size: .72rem; color: var(--muted); text-align: center;
  text-transform: uppercase; }
.tape-bar { grid-column: 1 / -1; display: flex; height: 10px; border-radius: 6px; overflow: hidden; margin-top: .5rem;
  border: 1.5px solid var(--line); gap: 0; }
.tape-bar i { display: block; height: 100%; }
.tape-bar i + i { border-left: 1.5px solid var(--line); }
.together { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.together div { border-radius: 20px; padding: 1rem; text-align: center; color: #1F1A17;
  border: 2px solid var(--line); box-shadow: 3px 3px 0 var(--line); }
.together div:nth-child(1) { background: var(--green); } .together div:nth-child(2) { background: var(--blue); }
.together b { display: block; font-size: 2.3rem; font-weight: 800; line-height: 1; letter-spacing: -.04em; }
.together span { font-weight: 700; letter-spacing: .06em; font-size: .74rem; text-transform: uppercase; }

/* Butterfly charts: one person to the left, the other to the right */
.fly { margin-top: .2rem; }
.fly-h { display: grid; grid-template-columns: 1fr 4.6rem 1fr; font-weight: 800; font-size: .95rem; padding-bottom: .45rem; }
.fly-h span:first-child { text-align: right; color: var(--pa-ink); } .fly-h span:last-child { color: var(--pb-ink); }
.fly-r { display: grid; grid-template-columns: 1fr 4.6rem 1fr; align-items: center; height: 1.75rem; }
.fly-l { text-align: center; font-weight: 700; font-size: .85rem; color: var(--muted); }
.fly-l .st { font-size: .62rem; letter-spacing: 0; }
.fly-a, .fly-b { display: flex; align-items: center; gap: .35rem; font-size: .74rem; font-weight: 600; color: var(--muted); }
.fly-a { flex-direction: row-reverse; }
.fly-a i, .fly-b i { display: block; height: 1rem; min-width: 3px; border: 1.5px solid var(--line); box-sizing: border-box; }
.fly-a i[style^="width:0.0%"], .fly-b i[style^="width:0.0%"] { display: none; }
.fly-a i { background: var(--pa); border-radius: 6px 3px 3px 6px; }
.fly-b i { background: var(--pb); border-radius: 3px 6px 6px 3px; }

@media (prefers-reduced-motion: reduce) {
  .ticket, .ring { animation: none !important; }
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

# ---------- Colour themes ----------
# Each person picks a background (four light ones) or "After dark" in Settings. Everything is drawn with
# these CSS variables, so a theme is just a different set of values.
COLOUR_THEMES = {"lavender": "Lavender", "butter": "Butter", "blush": "Blush", "periwinkle": "Periwinkle", "dark": "Dark"}
THEME_BG = {"lavender": "#ECE7F7", "butter": "#FBF1D3", "blush": "#F9E7E8", "periwinkle": "#DCE3FA"}
LIGHT = {"surface": "#FFFFFF", "line": "#1F1A17", "text": "#1F1A17", "muted": "#5C544E", "track": "rgba(31,26,23,.12)",
         "butter": "#F6D776", "pink": "#F49AB8", "blue": "#8EA2F2", "green": "#8DD3AE",
         "cta-bg": "#1F1A17", "cta-fg": "#F6D776", "nav-bg": "#1F1A17", "nav-fg": "#F4F0FA",
         "accent-ink": "#B8336A", "pa": "#F49AB8", "pb": "#8EA2F2", "pa-ink": "#B8336A", "pb-ink": "#3550C4"}
DARK = {"bg": "#1D1828", "surface": "#2A2338", "line": "#0D0B13", "text": "#F6F1FB", "muted": "#BDB4CC",
        "track": "rgba(246,241,251,.16)", "butter": "#F6DE8D", "pink": "#F2A7C3", "blue": "#A8B5FF", "green": "#A3E3C8",
        "cta-bg": "#F6DE8D", "cta-fg": "#1D1828", "nav-bg": "#2A2338", "nav-fg": "#F6F1FB",
        "accent-ink": "#F2A7C3", "pa": "#F2A7C3", "pb": "#A8B5FF", "pa-ink": "#F2A7C3", "pb-ink": "#A8B5FF"}


def theme_tokens() -> dict:
    ss = st.session_state
    ss.setdefault("theme", "lavender")
    ss.setdefault("auto_dark", True)
    theme = ss.get("theme") or "lavender"
    dark = theme == "dark" or (ss.get("auto_dark") and ss.get("phone_dark"))
    if dark:
        return {**DARK, "is-dark": "1"}
    return {**LIGHT, "bg": THEME_BG.get(theme, THEME_BG["lavender"]), "is-dark": "0"}


PREFS_JS = """
export default function(component) {
  const { data, setStateValue } = component;
  const KEY = 'double-feature-prefs';
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const dark = !!(mq && mq.matches);
  let saved = {};
  try { saved = JSON.parse(window.localStorage.getItem(KEY) || '{}') || {}; } catch (e) { saved = {}; }
  if (data && data.save) {
    try { window.localStorage.setItem(KEY, JSON.stringify(data.save)); } catch (e) {}
  }
  const seen = (data && data.seen) || {};
  if (!seen.loaded || seen.dark !== dark) setStateValue('env', { dark, saved });
}
"""
prefs_store = st.components.v2.component("prefs", js=PREFS_JS, isolate_styles=False)


def on_prefs():
    """The browser told us its saved settings and whether the phone is in dark mode."""
    env = (st.session_state.get("prefs") or {}).get("env") or {}
    st.session_state.phone_dark = bool(env.get("dark"))
    if not st.session_state.get("prefs_loaded"):
        st.session_state.prefs_loaded = True
        saved = env.get("saved") or {}
        if saved.get("theme") in COLOUR_THEMES:
            st.session_state.theme = saved["theme"]
        if isinstance(saved.get("auto"), bool):
            st.session_state.auto_dark = saved["auto"]
        if saved.get("posters") in ("art", "real"):
            st.session_state.poster_style = saved["posters"]


TOKENS = theme_tokens()
st.markdown("<style>:root{" + ";".join(f"--{k}:{v}" for k, v in TOKENS.items()) + "}</style>",
            unsafe_allow_html=True)
_ss = st.session_state
prefs_store(key="prefs", on_env_change=on_prefs,
            data={"seen": {"loaded": bool(_ss.get("prefs_loaded")), "dark": bool(_ss.get("phone_dark"))},
                  "save": ({"theme": _ss.get("theme") or "lavender", "auto": bool(_ss.get("auto_dark")),
                            "posters": _ss.get("poster_style") or "art"} if _ss.get("prefs_loaded") else None)})

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
    left, right = [], []  # one thick line, drawn as a single shape (small and fast)
    for i, (x, y) in enumerate(pts):
        x0, y0 = pts[max(i - 1, 0)]
        x1, y1 = pts[min(i + 1, len(pts) - 1)]
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy) or 1
        ox, oy = -dy / n * 1.1, dx / n * 1.1
        left.append((x + ox, y + oy))
        right.append((x - ox, y - oy))
    return [_P(left + right[::-1])]


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


def poster_svg(k, name, genres=(), avoid_gold=False):
    """(css background, text colour) for the generated poster."""
    memo = _memo()
    key = ("svg", k, name, genres, avoid_gold)
    val = memo.get(key)
    if val is None:
        if len(memo) > 20000:
            memo.clear()
        val = memo[key] = _poster_svg(k, name, genres, avoid_gold)
    return val


def _poster_svg(k, name, genres=(), avoid_gold=False):
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
    return f'<span class="dot" style="background:{COLOURS.get(name, "var(--pa)")}"></span>'


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


MAX_SHOWN = 120  # even with "Show all", a list never draws more than this (keeps phones quick)


def show_all(keys, key: str, limit: int):
    keys = list(keys)
    shown = keys[:MAX_SHOWN] if st.session_state.get(key) else keys[:limit]
    films = globals().get("cat")
    if films is not None:
        prefetch_posters([(k, str(films.at[k, "Name"]), films.at[k, "Year"]) for k in shown if k in films.index])
    return shown, len(keys) > limit


def show_all_toggle(keys, key: str):
    n = len(keys)
    st.toggle(f"Show all {n}" if n <= MAX_SHOWN else f"Show the top {MAX_SHOWN}", key=key)


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
        show_all_toggle(keys, key)


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
        show_all_toggle(keys, key)


def marquee(kicker: str, title: str, sub: str = ""):
    """The header on every screen: wordmark, Settings, a small label, a big title and a line under it."""
    md('<div class="marquee"><div class="mq-brand">double<span>·</span>feature</div>'
       + (f'<div class="mq-kick">{kicker}</div>' if kicker else "")
       + f'<div class="mq-title">{title}</div>'
       + (f'<div class="mq-sub">{sub}</div>' if sub else "") + '</div>')
    settings_menu()


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
        remember_posters(film_infos(items, wait=4.0))


def _pick_theme():
    st.session_state.theme = st.session_state.get("theme_pick") or st.session_state.get("theme") or "lavender"


def _pick_auto():
    st.session_state.auto_dark = bool(st.session_state.get("auto_pick"))


def settings_menu():
    """Per-person settings: colour theme, and (with film details on) poster style and streaming country.
    The theme lives in its own session keys, so it survives screens that don't show this menu."""
    ss = st.session_state
    with st.container(key="settings_wrap"):
        with st.popover("Settings", icon=":material/palette:"):
            ss.theme_pick = ss.get("theme") or "lavender"
            ss.auto_pick = bool(ss.get("auto_dark", True))
            st.pills("Colour", list(COLOUR_THEMES), key="theme_pick", format_func=COLOUR_THEMES.get, on_change=_pick_theme)
            st.toggle("Go dark when my phone is in dark mode", key="auto_pick", on_change=_pick_auto)
            if tmdb_on():
                ss.setdefault("poster_style", "art")
                st.segmented_control("Posters", ["art", "real"], key="poster_style",
                                     format_func={"art": "Minimal art", "real": "Real posters"}.get)
                region = current_region()
                st.selectbox("Streaming in", list(REGIONS), index=list(REGIONS).index(region),
                             format_func=REGIONS.get, key="region")


@st.cache_resource
def _memo():
    """Small shared memo (poster SVGs, fonts). Survives reruns, unlike module-level caches,
    because Streamlit re-runs this script as a fresh module every time."""
    return {}


@st.cache_resource
def _film_store():
    return {"lock": threading.Lock(), "films": {}}


@st.cache_resource
def _http():
    """One shared connection pool, so lookups reuse connections instead of a new handshake each time."""
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16))
    return sess


@st.cache_resource
def _tmdb_pool():
    """One small worker pool for every user, so the app as a whole stays under TMDB's rate limit."""
    return concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tmdb")


def _tmdb_get(ctx, path, **params):
    """GET from TMDB. Returns the JSON, or None if it failed (so nothing wrong gets cached)."""
    sess, key, store = ctx
    headers = {"accept": "application/json"}
    if key.startswith("eyJ"):  # the long "API read access token"
        headers["Authorization"] = f"Bearer {key}"
    else:                      # the short "API key"
        params["api_key"] = key
    for attempt in range(2):
        try:
            r = sess.get(TMDB_API + path, params=params, headers=headers, timeout=(3, 5))
        except requests.RequestException:
            return None
        if r.status_code == 429 and attempt == 0:  # rate limited: wait as asked (briefly), then try once more
            try:
                time.sleep(min(float(r.headers.get("Retry-After") or 1), 2))
            except ValueError:
                time.sleep(1)
            continue
        if r.status_code == 401:  # bad key: stop asking for a while
            store["down_until"] = time.time() + 600
            return None
        if not r.ok:
            return None
        try:
            return r.json()
        except ValueError:
            return None
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


def _fetch_info(ctx, name, year):
    """Look a film up on TMDB. Returns a dict, {} if not found, or None if TMDB couldn't be reached."""
    if FAKE_TMDB:
        return _fake_info(name, year)
    want = title_key(name) or str(name).casefold()

    def same_title(h):
        return want in (title_key(h.get("title", "")) or str(h.get("title", "")).casefold(),
                        title_key(h.get("original_title", "")) or str(h.get("original_title", "")).casefold())

    def year_off(h):
        y = (h.get("release_date") or "")[:4]
        return abs(int(y) - int(year)) if (year and y.isdigit()) else 5

    # Search by title first. Only if that finds no film with this title within a year either side, also
    # search with the year: Letterboxd sometimes dates a film by its festival premiere and TMDB by its
    # cinema release, so neither search alone is reliable.
    plain = _tmdb_get(ctx, "/search/movie", query=name)
    if plain is None:
        return None
    hits = [h for h in (plain.get("results") or []) if h.get("id")]
    if year and not any(same_title(h) and year_off(h) <= 1 for h in hits):
        by_year = _tmdb_get(ctx, "/search/movie", query=name, year=year)
        if by_year is None:
            return None
        seen = {h["id"] for h in hits}
        hits += [h for h in (by_year.get("results") or []) if h.get("id") and h["id"] not in seen]
    if not hits:
        return {}

    def rank(h):
        off = year_off(h)
        # A year either side counts as a match; among those, the film people have actually seen wins.
        return (not same_title(h), off > 1, -(h.get("vote_count") or 0), off, -(h.get("popularity") or 0))

    best = min(hits, key=rank)
    det = _tmdb_get(ctx, f"/movie/{best['id']}")
    prov = _tmdb_get(ctx, f"/movie/{best['id']}/watch/providers")
    if det is None or prov is None:  # partial failure: don't cache half the details
        return None
    providers = {}
    for cc, v in (prov.get("results") or {}).items():
        if cc not in REGIONS:  # only the countries the app offers, to keep memory small
            continue
        streams = sorted(v.get("flatrate", []) + v.get("free", []) + v.get("ads", []),
                         key=lambda p: p.get("display_priority", 99))
        names = list(dict.fromkeys(_clean_service(p.get("provider_name", "")) for p in streams if p.get("provider_name")))
        providers[cc] = {"stream": names, "rent": bool(v.get("rent") or v.get("buy")), "link": v.get("link") or ""}
    return {"id": best["id"], "poster": best.get("poster_path") or det.get("poster_path"),
            "runtime": det.get("runtime") or None,
            "genres": [g["name"] for g in det.get("genres") or [] if g.get("name")][:3], "providers": providers}


DETAIL_VERSION = 3  # bump when the lookup changes, so details cached by older code are fetched again


def _detail_key(name, year):
    # titles in other scripts (e.g. Japanese) have an empty title_key, so fall back to the plain title
    return f"v{DETAIL_VERSION}|{title_key(name) or str(name).strip().casefold()}|{year}"


def _fetch_and_store(ctx, name, year, ck):
    store = ctx[2]
    if store.get("down_until", 0) > time.time():  # queued before TMDB was switched off: skip quietly
        with store["lock"]:
            store["pending"].pop(ck, None)
        return None
    try:
        info = _fetch_info(ctx, name, year)
    except Exception:
        info = None
    now = time.time()
    with store["lock"]:
        store["pending"].pop(ck, None)
        if info is not None:
            if len(store["films"]) > 5000:
                store["films"].clear()
            store["films"][ck] = (now, info)
            store["failed"].pop(ck, None)
            store["last_ok"] = now
        else:
            store["failed"][ck] = now  # don't retry this film for a couple of minutes
            if len(store["failed"]) > 5000:
                store["failed"].clear()
            # Lots of failures in the last minute and no successes at all: an outage. Stop asking for 10 minutes.
            recent = [t for t in store.get("fail_times", []) if now - t < 60] + [now]
            store["fail_times"] = recent
            if len(recent) >= 25 and now - store.get("last_ok", 0) > 60:
                store["down_until"], store["fail_times"] = now + 600, []
    return info


def film_infos(items, limit=150, wait=8.0):
    """items: [(key, name, year)]. Returns {key: info} for whatever is known or arrives within `wait`
    seconds. Slower lookups carry on in the background and show up on the next rerun."""
    if not tmdb_on() or not items:
        return {}
    store, now, out, todo = _film_store(), time.time(), {}, []
    store.setdefault("pending", {})
    store.setdefault("failed", {})
    for k, name, year in items:
        y = _norm_year(year)
        ck = _detail_key(name, y)
        hit = store["films"].get(ck)
        if hit and now - hit[0] < DETAIL_TTL:
            out[k] = hit[1]
        elif now - store["failed"].get(ck, 0) > 120:
            todo.append((k, name, y, ck))
    if not todo or store.get("down_until", 0) > now:  # TMDB failing (bad key or outage): don't keep retrying
        return out
    ctx = (_http(), str(_secret("TMDB_API_KEY") or ""), store)
    futs = {}
    with store["lock"]:
        for k, name, y, ck in todo[:limit]:
            fut = store["pending"].get(ck)
            if fut is None:  # not already being looked up for someone else
                fut = store["pending"][ck] = _tmdb_pool().submit(_fetch_and_store, ctx, name, y, ck)
            futs[k] = fut
    done, _ = concurrent.futures.wait(list(futs.values()), timeout=wait)
    for k, fut in futs.items():
        if fut in done and fut.result() is not None:
            out[k] = fut.result()
    return out


@st.fragment(run_every=3)
def details_catchup(items):
    """Some details were still loading when the page was drawn: redraw once they've arrived."""
    pending = _film_store().get("pending", {})
    if not any(_detail_key(n, _norm_year(y)) in pending for _, n, y in items):
        st.rerun(scope="app")


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


def _load_font(name, size):
    memo = _memo()
    key = ("font", name, size)
    font = memo.get(key)
    if font is None:
        try:
            font = ImageFont.truetype(str(SHARE_FONTS / f"{name}.ttf"), size)
        except OSError:
            font = False  # missing file: remembered, so we don't keep trying
        memo[key] = font
    return font or None


def _covers(font, text) -> bool:
    """True if the font has a real glyph for every character (missing ones would print as boxes).
    Checked with Pillow's basic text engine, which draws a missing character as the font's 'no glyph' box."""
    memo = _memo()
    probe_key = ("probe", font.path)
    probe = memo.get(probe_key)
    if probe is None:
        try:
            probe = ImageFont.truetype(font.path, 24, layout_engine=ImageFont.Layout.BASIC)
            nd = bytes(probe.getmask("\U000E0FFF"))
        except Exception:
            return True
        probe = memo[probe_key] = (probe, nd)
    probe_font, notdef = probe
    for ch in set(text):
        if ch.isspace():
            continue
        key = ("glyph", font.path, ch)
        ok = memo.get(key)
        if ok is None:
            ok = memo[key] = bytes(probe_font.getmask(ch)) != notdef
        if not ok:
            return False
    return True


def _missing(font, text) -> set:
    """Characters in text that this font can't draw."""
    return {ch for ch in set(text) if not ch.isspace() and not _covers(font, ch)}


def _fit(name, size, text):
    """(font, text) for share images: the brand font if it can draw everything, else a fallback font.
    Characters no available font has (e.g. Japanese) are left out rather than printed as boxes."""
    options = [_load_font(n, size) for n in
               ((name, "fallback-serif", "fallback-sans") if name.startswith("fraunces") else (name, "fallback-sans"))]
    options = [f for f in options if f is not None]
    if not options:
        return _font(name, size), text
    best = min(options, key=lambda f: len(_missing(f, text)))  # first font with the fewest gaps
    gaps = _missing(best, text)
    if gaps:
        text = " ".join("".join(ch for ch in text if ch not in gaps).split())
    return best, text


def _font(name, size, text=""):
    """Share-image font. If the font files are missing entirely, Pillow's built-in font is used, never an error."""
    font = _fit(name, size, text)[0] if text else _load_font(name, size)
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            font = ImageFont.load_default()
    return font


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
    words, lines, cur = [], [], ""
    for w in text.split():  # break up single words too long for a line
        while draw.textlength(w, font=font) > max_w and len(w) > 1:
            cut = len(w) - 1
            while cut > 1 and draw.textlength(w[:cut], font=font) > max_w:
                cut -= 1
            words.append(w[:cut])
            w = w[cut:]
        words.append(w)
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


def _poster_image(k, name, w, h, year=None, genres=None):
    """The same screen-print poster as in the app, drawn with Pillow."""
    genres = _known_genres(name, year, k) if genres is None else genres
    _, pal, _ = poster_design(str(k), str(name), genres)
    top, bottom, _, ink = (_hex(x) for x in PALETTES[pal])
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):  # vertical gradient
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    img, _ = draw_poster_art(img, str(k), str(name), genres)
    d = ImageDraw.Draw(img)
    f, title = _fit("bricolage-800", int(w * .11), name)
    y = int(h * .07)
    for line in _wrap(d, title, f, w * .82, 4) if title else []:
        d.text((w * .09, y), line, font=f, fill=ink)
        y += int(w * .125)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(w * .04), fill=255)
    out = Image.new("RGBA", (w, h))
    out.paste(img, (0, 0), mask)
    return out


# Share images use the light lavender look whatever theme the phone is on, so they always read well
SH = {"bg": "#ECE7F7", "ink": "#1F1A17", "muted": "#5C544E", "white": "#FFFFFF",
      "butter": "#F6D776", "pink": "#F49AB8", "blue": "#8EA2F2", "green": "#8DD3AE"}


def _pill(d, cx, y, text, font, fill, pad_x=26, h=62, outline=6):
    """A sticker-style pill centred on cx, with its top at y."""
    w = d.textlength(text, font=font) + pad_x * 2
    d.rounded_rectangle([cx - w / 2, y, cx + w / 2, y + h], radius=h / 2, fill=_hex(fill), outline=_hex(SH["ink"]),
                        width=outline)
    box = font.getbbox(text)
    d.text((cx - (box[2] - box[0]) / 2 - box[0], y + (h - (box[3] - box[1])) / 2 - box[1]), text, font=font,
           fill=_hex(SH["ink"]))


def _share_canvas(kicker):
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), _hex(SH["bg"]))
    d = ImageDraw.Draw(img)
    ink = _hex(SH["ink"])
    # a few confetti stickers around the edges
    for x, y, r, col in ((90, 110, 16, "pink"), (980, 150, 13, "green"), (70, 640, 12, "blue"), (1000, 700, 16, "butter"),
                         (60, 1190, 13, "butter"), (1010, 1200, 12, "pink")):
        d.ellipse([x - r, y - r, x + r, y + r], fill=_hex(SH[col]), outline=ink, width=4)
    f, kick = _fit("bricolage-800", 40, kicker.upper())
    _pill(d, W / 2, 70, kick, f, SH["butter"])
    f = _font("bricolage-800", 44, "double·feature")
    brand = "double·feature"
    d.text((W / 2 - d.textlength(brand, font=f) / 2, H - 150), brand, font=f, fill=ink)
    f = _font("bricolage-500", 26)
    url = "double-feature.streamlit.app"
    d.text((W / 2 - d.textlength(url, font=f) / 2, H - 92), url, font=f, fill=_hex(SH["muted"]))
    return img, d


@st.cache_data(show_spinner=False, max_entries=100, ttl=24 * 3600)
def _poster_bytes(url):
    """The poster file itself (a small JPEG). Raises on failure, so failures are never cached."""
    r = _http().get(url, timeout=5)
    r.raise_for_status()
    Image.open(io.BytesIO(r.content)).verify()
    return r.content


def _fetch_poster(url, w, h):
    """Download a real poster for a share image, cropped to fill w×h with rounded corners."""
    if not str(url).startswith("https://"):
        return None
    try:
        src = Image.open(io.BytesIO(_poster_bytes(url))).convert("RGB")
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
    return _share_film_png(kicker, names_line, k, name, year, meta, stream, poster_url,
                           _known_genres(name, year, k))


@st.cache_data(show_spinner=False, max_entries=60, ttl=6 * 3600)
def _share_film_png(kicker, names_line, k, name, year, meta, stream, poster_url, genres):
    img, d = _share_canvas(kicker)
    W = img.width
    ink = _hex(SH["ink"])
    if names_line:
        f, names_line = _fit("bricolage-700", 46, names_line)
        d.text((W / 2 - d.textlength(names_line, font=f) / 2, 160), names_line, font=f, fill=ink)
    ft, title = _fit("bricolage-800", 70, name)
    lines = _wrap(d, title, ft, W - 200, 2) if title else []
    ph = 660 - 80 * max(len(lines) - 1, 0)  # a two-line title gets a shorter poster so nothing runs into the footer
    pw = int(ph * 2 / 3)
    px, py = int(W / 2 - pw / 2), 240
    d.rounded_rectangle([px + 14, py + 14, px + pw + 14, py + ph + 14], radius=22, fill=ink)  # hard sticker shadow
    poster_ = (_fetch_poster(poster_url, pw, ph) if poster_url else None) or _poster_image(k, name, pw, ph, year, genres)
    img.paste(poster_, (px, py), poster_)
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=int(pw * .04), outline=ink, width=6)
    y = py + ph + 40
    for line in lines:
        d.text((W / 2 - d.textlength(line, font=ft) / 2, y), line, font=ft, fill=ink)
        y += 74
    sub = " · ".join(x for x in (str(year or ""), meta) if x)
    if sub:
        f, sub = _fit("bricolage-700", 32, sub)
        d.text((W / 2 - d.textlength(sub, font=f) / 2, y + 8), sub, font=f, fill=_hex(SH["muted"]))
        y += 52
    if stream:
        f, stream = _fit("bricolage-700", 30, stream)
        _pill(d, W / 2, y + 16, stream, f, SH["green"], pad_x=22, h=54, outline=4)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


@st.cache_data(show_spinner=False, max_entries=100)
def share_taste_png(score, verdict, a, b, n_both, gap, tougher):
    img, d = _share_canvas("Taste match")
    W = img.width
    ink = _hex(SH["ink"])
    f, names = _fit("bricolage-700", 50, f"{a} + {b}")
    d.text((W / 2 - d.textlength(names, font=f) / 2, 170), names, font=f, fill=ink)
    cx, cy, R = W / 2, 540, 250
    d.ellipse([cx - R + 16, cy - R + 16, cx + R + 16, cy + R + 16], fill=ink)  # hard shadow
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=_hex(SH["white"]), outline=ink, width=6)
    band = 58
    if score > 0:
        d.pieslice([cx - R + 3, cy - R + 3, cx + R - 3, cy + R - 3], start=-90, end=-90 + 360 * score / 100,
                   fill=_hex(SH["pink"]))
    r2 = R - band
    d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=_hex(SH["white"]), outline=ink, width=6)
    big = _font("bricolage-800", 170)
    txt = f"{score}%"
    box = big.getbbox(txt)
    d.text((cx - (box[2] - box[0]) / 2 - box[0], cy - (box[3] - box[1]) / 2 - box[1]), txt, font=big, fill=ink)
    fv, verdict = _fit("bricolage-800", 68, verdict)
    d.text((W / 2 - d.textlength(verdict, font=fv) / 2, 830), verdict, font=fv, fill=ink)
    stats = [(str(n_both), "BOTH RATED", SH["butter"]), (f"{gap:.1f}★", "STAR GAP", SH["white"]),
             (tougher, "TOUGHER CRITIC", SH["white"])]
    for i, (v, lab, col) in enumerate(stats):
        x = W / 2 + (i - 1) * 310
        d.rounded_rectangle([x - 140, 950, x + 140, 1110], radius=28, fill=_hex(col), outline=ink, width=5)
        fv2, v = _fit("bricolage-800", 58 if len(v) < 8 else 38, v)
        d.text((x - d.textlength(v, font=fv2) / 2, 975), v, font=fv2, fill=ink)
        fl = _font("bricolage-700", 24)
        d.text((x - d.textlength(lab, font=fl) / 2, 1062), lab, font=fl, fill=_hex(SH["muted"]))
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
.tk-meta { font-size: .82rem; font-weight: 600; margin-top: .45rem; opacity: .8; }
.tk-stream { font-size: .82rem; font-weight: 800; margin-top: .2rem; }
.sw-meta { font-size: .8rem; font-weight: 500; color: var(--muted); margin-top: .15rem; }
.sw-stream { font-size: .8rem; font-weight: 800; margin-top: .15rem; color: var(--text); }
.share-btn { display: flex; align-items: center; justify-content: center; gap: .5rem; width: 100%; min-height: 3rem;
  border-radius: 16px; border: 2px solid var(--line); background: var(--green); color: #1F1A17; box-shadow: 3px 3px 0 var(--line);
  font-family: var(--sans); font-size: 1.02rem; font-weight: 800; cursor: pointer; -webkit-tap-highlight-color: transparent; }
.share-btn:active { transform: translate(2px, 2px); box-shadow: 1px 1px 0 var(--line); }
.credits { color: var(--muted); font-size: .74rem; font-weight: 500; text-align: center; margin-top: 2.2rem; }
.credits a { color: var(--muted) !important; }
.sw-super { width: 56px; height: 56px; align-self: center; background: var(--blue); color: #1F1A17; }
.sw-super[disabled] { opacity: .35; cursor: default; }
.stamp-super { left: 50%; top: 16%; transform: translateX(-50%) rotate(-6deg); background: var(--butter); color: #1F1A17;
  white-space: nowrap; }
.super-tag { display: inline-block; background: var(--butter); color: #1F1A17; border: 1.5px solid var(--line);
  border-radius: 999px; padding: 0 .45rem; font-size: .74rem; font-weight: 800; margin-left: .35rem; }
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


def get_room(code, kind=None):
    """The live room for a code, or None if it doesn't exist, has expired, or is a different kind."""
    if not code:
        return None
    room = _room_store()["rooms"].get(str(code).strip().upper())
    if not room or time.time() - room["created"] > ROOM_TTL or (kind and room.get("kind") != kind):
        return None
    room["seen"] = time.time()
    return room


def delete_room(code):
    with _room_store()["lock"]:
        _room_store()["rooms"].pop(str(code or "").strip().upper(), None)


def room_snapshot(code, kind=None):
    """A private copy taken under the lock, so drawing the page never trips over someone else's change."""
    with _room_store()["lock"]:
        room = get_room(code, kind)
        return copy.deepcopy(room) if room else None


def _new_code(store) -> str:
    """Clear out expired rooms and return an unused 4-letter code. Call with the lock held."""
    now = time.time()
    for c in [c for c, r in store["rooms"].items() if now - r["created"] > ROOM_TTL]:
        del store["rooms"][c]
    # Hard cap: if there are ever more than MAX_ROOMS live, drop the ones idle the longest
    rooms = store["rooms"]
    for c in sorted(rooms, key=lambda c: rooms[c].get("seen", rooms[c]["created"]))[:-MAX_ROOMS or None]:
        del rooms[c]
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
        room = get_room(code, "swipe")
        if not room or seat not in room["votes"] or room["match"]:
            return
        if key not in {c["key"] for c in room["deck"]}:
            return
        room["votes"][seat][key] = like
        if like and all(v.get(key) for v in room["votes"].values()):
            room["match"] = key


def new_round(code, keys=None, expect_round=None):
    """Start again, either with a subset of the deck (the 'maybe' pile) or the whole thing.
    expect_round stops a tap from a screen that's already out of date from resetting a newer round."""
    store = _room_store()
    with store["lock"]:
        room = get_room(code, "swipe")
        if not room or (expect_round is not None and room["round"] != expect_round):
            return
        pool_ = room["full_deck"]
        room["deck"] = [c for c in pool_ if c["key"] in keys] if keys else list(pool_)
        room["votes"] = {n: {} for n in room["names"]}
        room["match"], room["passed"] = None, set()
        room["round"] += 1


def keep_swiping(code, expect_match=None):
    store = _room_store()
    with store["lock"]:
        room = get_room(code, "swipe")
        if room and room["match"] and (expect_match is None or room["match"] == expect_match):
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
            f'<div class="stamp stamp-yes">YES!</div><div class="stamp stamp-no">NOPE</div>'
            f'<div class="stamp stamp-super">MUST WATCH</div>'
            f'<div class="sw-info"><div><div><b>{esc(str(c["year"] or ""))}</b> · {esc(c["why"])}</div>{meta}{strm}</div>'
            f'{lb}</div></div>')


def on_swipe(code, seat):
    v = (st.session_state.get(f"deck_{code}") or {}).get("swipe")
    if v and v.get("key"):
        cast_vote(code, seat, v["key"], bool(v.get("like")))


def choose_seat(code, name):
    st.session_state[f"seat_{code}"] = name
    with _room_store()["lock"]:
        room = get_room(code, "swipe")
        if room:
            room["joined"].add(name)


def leave_room():
    code = st.session_state.pop("room", None)
    st.session_state.pop(f"seat_{code}", None)
    st.query_params.pop("room", None)


@st.fragment(run_every=2)
def room_pulse(code, seat, seen):
    room = get_room(code, "swipe")
    if not room:
        st.rerun(scope="app")
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
    md(f'<div class="sw-status"><span class="live"></span><b>'
       f'{esc(other)}</b> {status}</div>')


def swipe_view(code, host=False):
    room = room_snapshot(code, "swipe")
    if not room:
        note("<b>That swipe session has ended.</b> Sessions last up to 12 hours. "
             "Start a new one from the Swipe tab.")
        st.button("OK", on_click=leave_room)
        return
    names = room["names"]
    COLOURS.update({names[0]: "var(--pa)", names[1]: "var(--pb)"})
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
            col.button(f"I'm {n}" + (" (rejoin)" if taken else ""), key=f"seat_btn_{n}", width="stretch",
                       type="secondary" if taken else "primary", on_click=choose_seat, args=(code, n))
        if room["joined"]:
            st.caption("Already joined on another phone? Only rejoin if this is that person's phone.")
        return

    other = next(n for n in names if n != seat)
    mine = room["votes"][seat]
    match = room["match"]
    room_pulse(code, seat, room_state(room, seat))

    if match:
        c = next(c for c in room["deck"] if c["key"] == match)
        md(f'<div class="its-match"><div class="im-k">YES! YES!</div>'
           f'<div class="im-t">It\'s a <em>match!</em></div>'
           f'<div class="im-n">{esc(names[0])} and {esc(names[1])} both swiped right. Tonight\'s film is…</div></div>')
        why = esc(c["why"]) + "".join(f'<div class="{cls}">{esc(c[f])}</div>'
                                      for f, cls in (("meta", "tk-meta"), ("stream", "tk-stream")) if c.get(f))
        md(ticket(c["key"], c["name"], c["year"], c["uri"], why, kicker="Matched for tonight"))
        share_button(lambda: share_film_png("It's a match", f"{names[0]} & {names[1]}", c["key"], c["name"], c["year"],
                                    c.get("meta", ""), c.get("stream", ""), real_poster_url(c["key"], "xl")),
                     "double-feature-match.jpg", f"It's a match: {c['name']} 🎬", key=f"share_match_{code}",
                     label="Share the match")
        st.button("Keep swiping for another", width="stretch", on_click=keep_swiping, args=(code, match))
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
    if not maybes and room["passed"]:
        msg = "That's the whole deck, and you've already seen every match in it."
    elif not maybes:
        msg = "You passed on everything. Tough crowd."
    elif len(maybes) < len(deck):
        msg = f"{len(maybes)} films got a yes from one of you. Swipe on just those to settle it?"
    else:
        msg = "Every film got a yes from one of you, just never both at once. Go again?"
    note(f"<b>No match this round.</b> {msg}")
    if maybes and len(maybes) < len(deck):
        st.button("Swipe the maybes", type="primary", width="stretch", on_click=new_round,
                  args=(code, maybes, room["round"]))
    st.button("Start over with the whole deck", width="stretch", on_click=new_round, args=(code, None, room["round"]))


SWIPE_CSS = """
<style>
.room { text-align: center; border-radius: 22px; padding: 1.1rem 1rem 1rem; margin: .4rem 0 .6rem;
  background: var(--surface); border: 2px solid var(--line); box-shadow: 4px 4px 0 var(--line); }
.room-k { font-weight: 700; letter-spacing: .14em; font-size: .75rem; color: var(--muted); text-transform: uppercase; }
.room-code { display: inline-block; margin: .55rem 0 .5rem; padding: .35rem .7rem .3rem .95rem; border-radius: 16px;
  background: var(--butter); color: #1F1A17; border: 2px solid var(--line); font-size: 2.6rem; font-weight: 800;
  letter-spacing: .28em; line-height: 1.1; }
.room-n { color: var(--muted); font-size: .9rem; font-weight: 500; line-height: 1.45; }
.sw-head { display: flex; justify-content: space-between; align-items: center; font-size: .9rem; font-weight: 600;
  color: var(--muted); margin: .5rem 0 .5rem; }
.sw-head b { color: var(--text); font-weight: 800; }
.sw-head span:last-child { font-weight: 800; color: var(--text); }
.sw-prog { height: 10px; border-radius: 6px; background: var(--surface); border: 2px solid var(--line); overflow: hidden;
  margin-bottom: 1rem; }
.sw-prog i { display: block; height: 100%; background: var(--pink); }
.sw-stack { position: relative; height: min(460px, calc(100svh - 250px), 122vw); aspect-ratio: 2 / 3; margin: 0 auto; }
.sw-card { position: absolute; inset: 0; border-radius: 24px; overflow: hidden; touch-action: pan-y; user-select: none;
  -webkit-user-select: none; cursor: grab; will-change: transform; background: var(--surface);
  border: 2px solid var(--line); box-shadow: 5px 5px 0 var(--line); }
.sw-card.top { z-index: 2; animation: sw-in .3s ease-out both; }
.sw-card.next { z-index: 1; transform: rotate(5deg) translate(10px, 6px); pointer-events: none; }
@keyframes sw-in { from { transform: scale(.96); } }
.sw-card .poster { position: absolute; inset: 0; width: 100%; height: 100%; aspect-ratio: auto; border-radius: 0;
  border: 0; box-shadow: none; }
.p-xl .p-t { font-size: 2.1rem; -webkit-line-clamp: 5; padding: 11% 10% 0; line-height: 1; }
.p-xl .p-y { display: none; }
.sw-info { position: absolute; left: 0; right: 0; bottom: 0; padding: .8rem 1rem .9rem; z-index: 3;
  display: flex; justify-content: space-between; align-items: flex-end; gap: .6rem;
  background: var(--surface); border-top: 2px solid var(--line); font-size: .86rem; font-weight: 500; color: var(--text); }
.sw-info b { font-weight: 800; }
.sw-lb { flex: 0 0 auto; font-weight: 800; font-size: .8rem; color: var(--text) !important;
  text-decoration: none !important; padding: .3rem .65rem; border-radius: 999px; border: 2px solid var(--line); }
.stamp { position: absolute; top: 34%; z-index: 4; font-family: var(--sans); font-size: 2rem; font-weight: 800;
  padding: .1rem .8rem; border: 2px solid var(--line); border-radius: 14px; opacity: 0; pointer-events: none;
  color: #1F1A17; letter-spacing: -.01em; }
.stamp-yes { left: 1rem; background: var(--green); transform: rotate(-12deg); }
.stamp-no { right: 1rem; background: var(--surface); color: var(--text); transform: rotate(12deg); }
.sw-btns { display: flex; justify-content: center; align-items: center; gap: 1.3rem; margin: 1.4rem 0 .5rem; }
.sw-btn { width: 68px; height: 68px; border-radius: 50%; border: 2px solid var(--line); display: grid; place-items: center;
  cursor: pointer; box-shadow: 3px 3px 0 var(--line); transition: transform .15s; -webkit-tap-highlight-color: transparent; }
.sw-btn:active { transform: translate(2px, 2px); box-shadow: 1px 1px 0 var(--line); }
.sw-no { background: var(--surface); color: var(--text); }
.sw-yes { background: var(--pink); color: #1F1A17; }
.sw-hint { text-align: center; color: var(--muted); font-size: .82rem; font-weight: 500; margin-bottom: .6rem; }
.sw-status { display: flex; align-items: center; justify-content: center; gap: .1rem; font-size: .88rem; font-weight: 600;
  color: var(--muted); padding: .45rem .9rem; border-radius: 999px; background: var(--surface);
  border: 2px solid var(--line); width: fit-content; margin: .3rem auto .7rem; }
.sw-status b { color: var(--text); font-weight: 800; margin-right: .3rem; }
.live { width: .5rem; height: .5rem; border-radius: 50%; background: #4FAE7C; margin-right: .5rem;
  box-shadow: 0 0 0 0 rgba(79,174,124,.6); animation: live 1.8s infinite; }
@keyframes live { 70% { box-shadow: 0 0 0 7px rgba(79,174,124,0); } 100% { box-shadow: 0 0 0 0 rgba(79,174,124,0); } }
.its-match { text-align: center; margin: .6rem 0 1.1rem; animation: pop .6s cubic-bezier(.2,.9,.25,1.3) both; }
@keyframes pop { from { opacity: 0; transform: scale(.85); } }
.im-k { display: inline-block; background: var(--green); color: #1F1A17; border: 2px solid var(--line); border-radius: 12px;
  padding: .1rem .8rem; font-weight: 800; font-size: 1rem; transform: rotate(-4deg); }
.im-t { font-size: 2.7rem; font-weight: 800; line-height: .95; letter-spacing: -.045em; margin: .55rem 0 .35rem; }
.im-t em { font-style: normal; color: var(--accent-ink); }
.im-n { color: var(--muted); font-size: .95rem; font-weight: 500; }
.or { display: flex; align-items: center; gap: .8rem; margin: 1.4rem 0 .2rem; color: var(--muted);
  font-weight: 700; letter-spacing: .14em; font-size: .78rem; text-transform: uppercase; }
.or:before, .or:after { content: ""; flex: 1; height: 2px; background: var(--line); opacity: .25; }
.how-n { color: var(--muted); font-size: .82rem; margin: -.3rem 0 .2rem; }
.how { display: grid; gap: 10px; margin: .9rem 0 1.2rem; }
.how div { display: flex; gap: .8rem; align-items: center; border-radius: 18px; padding: .8rem .9rem;
  background: var(--surface); border: 2px solid var(--line); font-size: .93rem; font-weight: 600; line-height: 1.35; }
.how > div > b { flex: 0 0 2rem; height: 2rem; border-radius: 50%; display: grid; place-items: center; font-weight: 800;
  font-size: 1rem; background: var(--butter); color: #1F1A17; border: 2px solid var(--line); box-sizing: border-box; }
.how > div:nth-child(2) > b { background: var(--pink); } .how > div:nth-child(3) > b { background: var(--blue); }
.how > div:nth-child(4) > b { background: var(--green); }
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


def safe_load(raw):
    """Parse a slimmed upload; None if it can't be read for any reason."""
    try:
        return load_export(raw) if raw else None
    except (zipfile.BadZipFile, ValueError, TypeError, KeyError, UnicodeDecodeError, RuntimeError, OSError):
        return None


def clean_name(name) -> str:
    """Names are shown to other people, so keep them short and free of markdown or HTML."""
    name = re.sub(r"[\[\]()<>*_`#|~\\{}!$^:/]", "", str(name or ""))
    return " ".join(name.split())[:24]


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
                name = _csv_name(f.name)
                if name in KEEP:
                    src[name] = f.getvalue()
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):  # not a zip, damaged, or password-protected
        return None
    if not src:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in src.items():
            df = _read_csv(data)
            df = df[[c for c in KEEP[name] if c in df.columns]]
            if df.shape[1] == 0:
                continue
            # a fixed timestamp keeps the bytes identical on every rerun, so the parsed export stays cached
            out.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), df.to_csv(index=False),
                         compress_type=zipfile.ZIP_DEFLATED)
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
    d = safe_load(raw)
    if not d or not d["found"]:
        return None, ("That doesn't look like a Letterboxd export. Upload the .zip from letterboxd.com → "
                      "Settings → Data → Export your data, or the watched.csv, ratings.csv and watchlist.csv inside it.")
    return raw, (clean_name(typed_name) or d["name"] or fallback)


def start_pair():
    raw, name = read_upload(st.session_state.get("sp_file"), st.session_state.get("sp_name", ""), "Person 1")
    if raw is None:
        st.session_state.sp_error = name
        return
    st.session_state.sp_error = ""
    st.session_state.pair = create_pair(name, raw)
    st.session_state.me = 0
    st.query_params["room"] = st.session_state.pair  # a reload brings this phone back to its pair


def finish_pair(code):
    raw, name = read_upload(st.session_state.get("pj_file"), st.session_state.get("pj_name", ""), "Person 2")
    if raw is None:
        st.session_state.pj_error = name
        return
    store = _room_store()
    with store["lock"]:
        room = get_room(code, "pair")
        if not room:
            st.session_state.pj_error = "That pair code has expired. Ask for a new one."
            return
        if room["slots"][1] is not None:
            st.session_state.pj_error = "Someone has already paired with this code."
            return
        if name.casefold() == room["slots"][0]["name"].casefold():
            name = f"{name} (2)"
        room["slots"][1] = {"name": name, "raw": raw}
    st.session_state.pj_error = ""
    st.session_state.pair, st.session_state.me = code, 1
    st.session_state.pop("pair_join", None)
    st.query_params["room"] = code


def rejoin_pair(code, i):
    with _room_store()["lock"]:
        room = get_room(code, "pair")
        if room:
            room.get("left", set()).discard(i)
    st.session_state.pair, st.session_state.me = code, i
    st.session_state.pop("pair_join", None)
    st.query_params["room"] = code


def unpair():
    """Leave the pair on this phone. Once nobody is using it (or it was never completed), the room
    and both exports are deleted straight away rather than waiting 12 hours."""
    code, me_ = st.session_state.get("pair"), st.session_state.get("me")
    if code is not None and me_ is not None:
        with _room_store()["lock"]:
            room = get_room(code, "pair")
            if room:
                room.setdefault("left", set()).add(me_)
                if room["slots"][1] is None or len(room["left"]) >= 2:
                    _room_store()["rooms"].pop(code, None)
    for k in ("pair", "me", "pair_join", "seen_swipe", "room", "qp_handled"):
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
        room = get_room(pair_code, "pair")
        if room:
            room["swipe"] = sc
    st.session_state.seen_swipe = sc
    choose_seat(sc, seat)


def end_pair_swipe(pair_code):
    with _room_store()["lock"]:
        room = get_room(pair_code, "pair")
        if room:
            _room_store()["rooms"].pop(room["swipe"] or "", None)
            room["swipe"] = None


@st.fragment(run_every=2)
def pair_pulse(code, seen):
    room = get_room(code, "pair")
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
.guide { display: grid; gap: 10px; margin-top: .8rem; }
.gstep { display: grid; grid-template-columns: 118px 1fr; gap: .8rem; align-items: center; padding: .7rem;
  border-radius: 18px; background: var(--surface); border: 2px solid var(--line); }
.gtext { font-size: .88rem; font-weight: 500; line-height: 1.4; color: var(--muted); }
.gtext b { color: var(--text); font-weight: 800; }
.gtext em { display: inline-grid; place-items: center; width: 1.45rem; height: 1.45rem; border-radius: 50%; font-style: normal;
  background: var(--butter); color: #1F1A17; border: 2px solid var(--line); box-sizing: border-box; font-weight: 800;
  font-size: .8rem; margin-right: .4rem; vertical-align: .05rem; }
.gmock { border-radius: 12px; background: #F4F2EE; padding: .45rem .45rem .5rem; color: #2a2f38; font-size: .56rem;
  line-height: 1.2; border: 2px solid #1F1A17; min-height: 92px;
  display: flex; flex-direction: column; gap: .28rem; overflow: hidden; }
.gm-url { display: flex; gap: .2rem; align-items: center; background: #fff; border-radius: 6px; padding: .2rem .3rem;
  font-size: .5rem; color: #555; white-space: nowrap; overflow: hidden; }
.gm-top { font-weight: 800; font-size: .66rem; }
.gm-in { background: #fff; border-radius: 4px; padding: .2rem .3rem; color: #888; border: 1px solid #d5d1c8; }
.gm-row { height: .5rem; border-radius: 3px; background: #d6d1c8; width: 85%; }
.gm-row.s { width: 60%; }
.gm-save { align-self: flex-start; background: #1F1A17; color: #fff; border-radius: 4px; padding: .18rem .45rem; font-weight: 700; }
.gm-tabs { display: flex; gap: .35rem; border-bottom: 1px solid #cfc9bd; padding-bottom: .2rem; color: #888; }
.gm-tabs .on { color: #1F1A17; font-weight: 800; box-shadow: 0 .25rem 0 -.08rem #F49AB8; }
.gm-btn { position: relative; align-self: center; margin-top: .35rem; background: #1F1A17; color: #fff; border-radius: 6px;
  padding: .35rem .5rem; font-weight: 700; white-space: nowrap; }
.gm-btn i { position: absolute; right: -.35rem; bottom: -.4rem; width: 1rem; height: 1rem; border-radius: 50%;
  background: rgba(244,154,184,.75); box-shadow: 0 0 0 0 rgba(244,154,184,.8); animation: tapping 1.6s infinite; }
@keyframes tapping { 70% { box-shadow: 0 0 0 .55rem rgba(244,154,184,0); } 100% { box-shadow: 0 0 0 0 rgba(244,154,184,0); } }
.gm-file { display: flex; gap: .35rem; align-items: center; background: #fff; border-radius: 8px; padding: .35rem;
  margin-top: .3rem; border: 1px solid #d5d1c8; }
.gm-file b { display: block; font-size: .55rem; } .gm-file small { color: #2E7D55; font-weight: 700; }
.gm-zip { background: #F6D776; color: #1F1A17; border-radius: 4px; padding: .3rem .25rem; font-weight: 800; font-size: .5rem; }
.gm-arrow { text-align: center; color: #B8336A; font-weight: 800; margin-top: .15rem; }
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
    uri = safe_uri(uri) or f"https://letterboxd.com/search/films/{urllib.parse.quote(name)}/"
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
    """Changes here reload every phone in the group (phase changes, new rounds, a new host)."""
    return (room["phase"], room["round"], room["host"])


def add_member(code, name):
    store = _room_store()
    with store["lock"]:
        room = get_room(code, "group")
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


def set_films(code, mid, films) -> bool:
    """Put this person's films in the pot. False if that's no longer possible (voting has started)."""
    store = _room_store()
    with store["lock"]:
        room = get_room(code, "group")
        if not room or mid not in room["members"] or room["phase"] != "lobby":
            return False
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
        return True


def start_group_vote(code, keys=None, expect_round=None):
    store = _room_store()
    with store["lock"]:
        room = get_room(code, "group")
        # only from the lobby or the results, and only once (a double tap would wipe everyone's votes)
        if not room or room["phase"] == "voting" or (expect_round is not None and room["round"] != expect_round):
            return
        deck = list(keys) if keys else list(room["films"])
        random.Random(f"{code}{room['round']}").shuffle(deck)
        room["deck"], room["phase"] = deck, "voting"
        room["votes"] = {m: {} for m in room["members"]}
        room["supers"] = {}  # one super-like per person per round
        room["round"] += 1


def end_group_vote(code):
    with _room_store()["lock"]:
        room = get_room(code, "group")
        if room and room["phase"] == "voting":
            room["phase"] = "results"


def back_to_lobby(code):
    with _room_store()["lock"]:
        room = get_room(code, "group")
        if room and room["phase"] != "lobby":
            room["phase"], room["deck"], room["votes"] = "lobby", [], {}
            room["round"] += 1


def group_vote(code, mid):
    v = (st.session_state.get(f"gdeck_{code}") or {}).get("swipe")
    if not (v and v.get("key")):
        return
    with _room_store()["lock"]:
        room = get_room(code, "group")
        if not room or room["phase"] != "voting" or v["key"] not in room["deck"] or mid not in room["members"]:
            return
        like = v.get("like")
        if like == "super":
            supers = room.setdefault("supers", {})
            if mid in supers:  # already used this round: count it as a normal yes
                like = True
            else:
                supers[mid] = v["key"]
        room["votes"].setdefault(mid, {})[v["key"]] = like if like == "super" else bool(like)
        _close_if_done(room)


def _close_if_done(room):
    """Show the results as soon as everyone still in the group has voted on every film. Call with the lock held."""
    voters = [m for m in room["members"] if m in room["votes"]]
    if room["phase"] == "voting" and voters and all(len(room["votes"][m]) >= len(room["deck"]) for m in voters):
        room["phase"] = "results"


def remove_member(code, mid):
    """Someone left: drop them (and their unvoted films, in the lobby), hand over hosting if needed,
    and delete the room once nobody's left."""
    with _room_store()["lock"]:
        room = get_room(code, "group")
        if not room or mid not in room["members"]:
            return
        gone = room["members"].pop(mid)  # their votes stay: they still count towards the result
        if room["phase"] == "lobby":
            for f in gone["films"]:
                entry = room["films"].get(f)
                if entry:
                    entry["by"] = [b for b in entry["by"] if b != mid]
                    if not entry["by"]:
                        del room["films"][f]
        if not room["members"]:
            _room_store()["rooms"].pop(room["code"], None)
            return
        if room["host"] == mid:
            room["host"] = next(iter(room["members"]))
        _close_if_done(room)


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
    if st.session_state.get("group") and st.session_state.get("gid"):
        remove_member(st.session_state.group, st.session_state.gid)
    for k in ("group", "gid", "group_join", "qp_handled"):
        st.session_state.pop(k, None)
    st.query_params.pop("room", None)


def host_group():
    name = clean_name(st.session_state.get("gh_name", "")) or "Host"
    code, mid = create_group(name)
    st.session_state.group, st.session_state.gid = code, mid
    st.query_params["room"] = code  # a reload brings this phone back to the group


def join_group(code):
    name = clean_name(st.session_state.get("gj_name", ""))
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
    st.query_params["room"] = code


def rejoin_group(code, mid):
    st.session_state.group, st.session_state.gid = code, mid
    st.session_state.pop("group_join", None)
    st.query_params["room"] = code


def save_my_films(code, mid, films):
    if not set_films(code, mid, films):
        st.session_state.gf_error = "Too late to add films: the vote has already started."
        return
    st.session_state.pop("gf_error", None)
    st.session_state.gf_saved = True


def by_names(room, entry):
    names = [room["members"][m]["name"] for m in entry["by"] if m in room["members"]]
    return "Brought by " + (", ".join(names) if names else "someone")


@st.fragment(run_every=2)
def group_pulse(code, mid, seen):
    room = room_snapshot(code, "group")
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
    if st.session_state.get("gf_error"):
        st.error(st.session_state.gf_error)
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
    room = room_snapshot(code, "group")
    if not room or mid not in room["members"]:
        with header:
            marquee("Group night", "Movie night<em>?</em>", "")
        note("<b>That movie night has ended.</b> They last 12 hours. Start a new one from the home screen.")
        st.button("OK", on_click=leave_group)
        return
    me_ = room["members"][mid]
    is_host = mid == room["host"]
    host = room["members"][room["host"]]["name"]
    with header:
        marquee("Movie night", f"At {esc(host)}'s<em>.</em>", f"Group night<i>✦</i>Room <b>{code}</b>")
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
            md(f'<div class="its-match"><div class="im-k">The votes are in</div>'
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
            md(f'<div class="its-match"><div class="im-k">The votes are in</div>'
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
                            f'<div class="row-m"><div class="vs-gap">{y}'
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
    room = room_snapshot(code, "group")
    with header:
        if room:
            host = room["members"][room["host"]]["name"]
            marquee("Movie night", f"At {esc(host)}'s<em>.</em>", f"Room <b>{code}</b>")
        else:
            marquee("Group night", "Movie night<em>?</em>", "")
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
.gms { display: flex; flex-wrap: wrap; gap: 8px; margin: .5rem 0 1rem; }
.gm { display: flex; align-items: baseline; gap: .45rem; padding: .4rem .8rem; border-radius: 999px;
  background: var(--surface); border: 2px solid var(--line); font-size: .9rem; }
.gm-n { color: var(--text); font-weight: 800; }
.gm-c { color: var(--muted); font-size: .8rem; font-weight: 600; }
.gbar { height: 10px; border-radius: 6px; background: var(--surface); border: 1.5px solid var(--line); overflow: hidden; margin-top: .4rem; }
.gbar i { display: block; height: 100%; background: var(--pink); }
.reel-item .poster { width: 100%; }
.tap-card { margin-top: .2rem; }
.st-key-scatter_card { background: var(--surface); border: 2px solid var(--line); border-radius: 20px; padding: .6rem .7rem .2rem;
  box-shadow: 4px 4px 0 var(--line); }
[data-testid="stElementToolbar"] { display: none !important; }  /* chart toolbar covers text on phones */
/* Touch screens: no hover tooltip (it glitches on tap), the card under the chart does the job instead */
@media (hover: none) { #vg-tooltip-element { display: none !important; } }
@media (hover: hover) { .tap-hint { display: none; } }
.tap-hint { text-align: center; color: var(--muted); font-size: .84rem; font-weight: 500; margin-top: .6rem; }
</style>
"""
md(GROUP_CSS)


header = st.container(key="hdr")
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
pair = get_room(pair_code, "pair") if pair_code else None
if pair_code and not pair:
    unpair()
    st.session_state.join_error = "Your pairing has expired (they last 12 hours). Start a new one below."
paired = bool(pair and all(pair["slots"]) and me is not None)

# 1. Started a pair, waiting for the other person to upload
if pair and not paired:
    with header:
        marquee("Pairing up", f"{esc(pair['slots'][0]['name'])} <em>+</em> …",
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
    room = get_room(jc, "pair")
    with header:
        if room:
            marquee("Pairing up", f"{esc(room['slots'][0]['name'])} <em>+</em> you",
                    f"Pair code <b>{jc}</b>")
        else:
            marquee("Pairing up", "Two phones<em>.</em>", "One movie night")
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
        st.button(f"This is my code (I'm {room['slots'][0]['name']})", type="tertiary",
                  on_click=rejoin_pair, args=(jc, 0), key="rejoin_host")
    else:
        section("Which one are you?", kicker=f"Pair {jc}", first=True,
                note="This pair is already set up. Pick your name to open it on this phone.")
        cols = st.columns(2)
        for i, (col, slot) in enumerate(zip(cols, room["slots"])):
            col.button(slot["name"], key=f"rejoin_{i}", width="stretch", on_click=rejoin_pair, args=(jc, i))
    st.stop()

# 3. Joining a swipe session on a phone with no exports
if not paired and not have_both and st.session_state.get("room"):
    room = get_room(st.session_state.room, "swipe")
    with header:
        if room:
            marquee("Swipe night", f"{esc(room['names'][0])} <em>+</em> {esc(room['names'][1])}",
                    f"Room <b>{room['code']}</b><i>✦</i><b>{len(room['deck'])}</b> films in the deck")
        else:
            marquee("Swipe night", "Swipe till<br>you match<em>.</em>", "Two phones, one film")
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
            if mode == "group":
                marquee("Group night", "Movie night<em>?</em>",
                        "Everyone brings a few films, everyone swipes, and one film wins.")
            else:
                marquee("", "Movie<br>night<em>?</em>", "Swipe till you match. No more scrolling for an hour.")
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

da, db = safe_load(raw_a), safe_load(raw_b)
bad = [label for label, d in (("the first person", da), ("the second person", db)) if not d or not d["found"]]
if bad:
    st.error(
        f"Couldn't read {' or '.join(bad)}'s export. Upload the .zip from letterboxd.com → Settings → Data "
        "→ Export your data, or the watched.csv, ratings.csv and watchlist.csv files inside it."
    )
    st.stop()

# Names: whatever was typed, else the name on the Letterboxd profile, else a placeholder
A = clean_name(name_a) or da["name"] or "Person 1"
B = clean_name(name_b) or db["name"] or "Person 2"
if A.casefold() == B.casefold():
    B = f"{B} (2)"
COLOURS.update({A: "var(--pa)", B: "var(--pb)"})

# Paired: let this phone know when the other one starts a swipe session
if paired and pair["swipe"] and pair["swipe"] != st.session_state.get("seen_swipe"):
    st.session_state.seen_swipe = pair["swipe"]
    st.toast(f"{[A, B][1 - me]} started a swipe session. Open the Swipe tab to join in.")

pa, pb = summarise(da), summarise(db)
cat = build_catalog(da, db)
shared_wl = pa["watchlist"] & pb["watchlist"]
both_seen = pa["seen"] & pb["seen"]

with header:
    marquee("Now showing", f"{esc(A)} <em>+</em> {esc(B)}",
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
                           format_func=lambda d: f"{str(d)[2:]}s" if d >= 1930 else f"{d}s") if decades else []
    if chosen:
        pool = {k: why for k, why in pool.items()
                if pd.notna(cat.at[k, "Year"]) and int(cat.at[k, "Year"]) // 10 * 10 in chosen}

    # Film details: length, genre and where it's streaming (only when a TMDB key is set up)
    region, infos = current_region(), {}
    if tmdb_on() and pool:
        items_ = [(k, str(cat.at[k, "Name"]), cat.at[k, "Year"]) for k in pool]
        with st.spinner("Fetching film details…"):
            infos = film_infos(items_)
        late = [i for i in items_ if i[0] not in infos
                and _detail_key(i[1], _norm_year(i[2])) in _film_store().get("pending", {})]
        if late:  # only while lookups are actually still running, so this can't loop
            details_catchup(late)
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
    st.session_state.qp_handled = st.session_state.room


with t_swipe:
    code = st.session_state.get("room")
    sc = pair["swipe"] if paired else None
    deck = [{"key": k, "name": str(cat.at[k, "Name"]), "year": year_str(cat.at[k, "Year"]),
             "uri": str(cat.at[k, "Letterboxd URI"]) if pd.notna(cat.at[k, "Letterboxd URI"]) else "",
             "why": reason_plain(why), "meta": details_text(infos.get(k)),
             "stream": stream_text(infos.get(k), region)} for k, why in pool.items()]
    if paired and sc and get_room(sc, "swipe"):
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
    elif code and get_room(code, "swipe"):
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
                note=f'Each dot is a film. <span style="color:var(--pa-ink);font-weight:800">Pink</span>: {esc(A)} rated '
                     f'it higher. <span style="color:var(--pb-ink);font-weight:800">Blue</span>: {esc(B)} did. '
                     f'Yellow: you agreed.')
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
        points = alt.Chart(plot).mark_circle(stroke=TOKENS["line"], strokeWidth=1.5).encode(
            x=alt.X("aj:Q", title=f"{A} →", scale=scale, axis=alt.Axis(**ax)),
            y=alt.Y("bj:Q", title=f"{B} →", scale=scale, axis=alt.Axis(**ax)),
            color=alt.Color("Higher:N", scale=alt.Scale(domain=[A, B, "Agreed"], range=[TOKENS["pa"], TOKENS["pb"], TOKENS["butter"]]),
                            legend=None),
            size=alt.condition(tap, alt.value(260), alt.value(110)),
            opacity=alt.value(1),
            strokeWidth=alt.condition(tap, alt.value(3.5), alt.value(1.5)),
            stroke=alt.value(TOKENS["line"] if TOKENS["is-dark"] == "0" else TOKENS["text"]),
            tooltip=["Film", alt.Tooltip("a:Q", title=A), alt.Tooltip("b:Q", title=B)],
        ).add_params(tap)
        diagonal = alt.Chart(pd.DataFrame({"aj": [0.25, 5.25], "bj": [0.25, 5.25]})).mark_line(
            strokeDash=[4, 5], color=TOKENS["muted"], strokeWidth=1.5).encode(x="aj:Q", y="bj:Q")
        chart = (diagonal + points).properties(height=330).configure(
            background="transparent", font="Bricolage Grotesque").configure_view(stroke=None).configure_axis(
            labelColor=TOKENS["muted"], titleColor=TOKENS["text"], gridColor=TOKENS["track"], domain=False,
            ticks=False, labelFontSize=12, titleFontSize=13, titleFontWeight=700, labelPadding=8, titlePadding=10)
        with st.container(key="scatter_card"):
            event = st.altair_chart(chart, theme=None, on_select="rerun", selection_mode="tap", key="taste_scatter")
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
            shown_loved, more_loved = show_all(loved, "all_loved", 20)
            items = "".join(
                f'<div class="reel-item">{poster(k)}<div class="reel-cap">'
                f'{rating_line(A, ra[k])}{rating_line(B, rb[k])}</div></div>' for k in shown_loved)
            md(f'<div class="reel">{items}</div>')
            if more_loved:
                show_all_toggle(loved, "all_loved")
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
    if pfrm["ratings"].empty:
        note(f"<b>Nothing to go on yet.</b> {esc(frm)} hasn't rated any films on Letterboxd.")
    elif recs.empty:
        note(f"<b>All caught up.</b> {esc(to)} has already seen everything {esc(frm)} rated "
             f"{stars(threshold)} or higher.")
    else:
        order = sorted(recs.index, key=lambda k: (-recs[k], str(cat.at[k, "Name"]).casefold()))
        prefetch_posters([(k, str(cat.at[k, "Name"]), cat.at[k, "Year"]) for k in order[:60]])
        md(f'<div class="sec-n" style="margin:.4rem 0 .2rem">{dot(frm)}<b style="color:var(--text)">'
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
    return (f'<div class="tape-bar"><i style="width:{a:.1f}%;background:var(--pa)"></i>'
            f'<i style="flex:1;background:var(--pb)"></i></div>')


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
        f'<div class="tape-r"><b style="color:var(--pa-ink)">{fmt.format(x) if x is not None else "–"}</b>'
        f'<span>{label}</span><b style="color:var(--pb-ink)">{fmt.format(y) if y is not None else "–"}</b>'
        f'{pct_split(x, y)}</div>'
        for label, x, y, fmt in rows)
    tape += (f'<div class="tape-r"><b style="color:var(--pa-ink)">{fav_decade(pa)}</b><span>Favourite decade</span>'
             f'<b style="color:var(--pb-ink)">{fav_decade(pb)}</b></div>')
    section("Tale of the tape", kicker="Head to head", first=True)
    md(f'<div class="tape"><div class="tape-h"><span style="color:var(--pa-ink)">{esc(A)}</span><i>vs</i>'
       f'<span style="color:var(--pb-ink)">{esc(B)}</span></div>{tape}</div>'
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
        md(butterfly([(star_bar(lv), float(sa.get(lv, 0)), float(sb.get(lv, 0))) for lv in levels],
                     fmt=lambda v: f"{v:.0%}" if v else ""))

attribution()
