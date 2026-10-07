# 🎬 Double Feature

**Two Letterboxd accounts, one movie night.**

Double Feature compares two people's Letterboxd data and helps them pick something to watch together. It's built for phones and styled like an old movie palace.

👉 **[Open the app](https://YOUR-APP-NAME.streamlit.app)**

![Double Feature on a phone: the Pick, Swipe, Taste and Stats tabs](preview.png)

---

## What it does

| Tab | What you get |
| --- | --- |
| **Pick** | Films on both your watchlists. Tap the button and it picks one at random, printed on a ticket. Filters let you add films one of you rated 4★+ that the other wants to see, or narrow it down by decade. |
| **Swipe** | Like a dating app, but for films. You each swipe through the same deck on your own phones, and the first film you both swipe right on is tonight's film. |
| **Taste** | Your taste-match score, a chart of every film you've both rated, the films you both loved and your biggest disagreements. |
| **Swaps** | Films one of you rated highly that the other hasn't seen yet. |
| **Stats** | A head-to-head comparison: films logged, average rating, favourite decade and how you each hand out stars. |

## How to use it

### 1. Export your Letterboxd data

Go to **[letterboxd.com/settings/data](https://letterboxd.com/settings/data)** and click **Export your data**. You'll get a `.zip` file.

> The export is only available on the Letterboxd **website**, not in the phone app. On a phone, open the site in your browser.

### 2. Pair up

**Each on your own phone (the default)**

1. One of you opens the app, uploads your export and taps **Get a pair code**.
2. Send the other person the link or the four-letter code.
3. They open it, upload their own export, and both phones load the full app.

**Both on one phone**

Pick **Both on this phone** and upload both exports. You can still start a swipe session from the Swipe tab, and the other person joins it on their phone with the code.

### Tips

- If your export got unzipped, upload `watched.csv`, `ratings.csv` and `watchlist.csv` instead of the zip.
- Names are optional. If you leave yours blank, the app uses the name on your Letterboxd profile.
- To try it out on your own, choose **Both on this phone** and upload your export into both boxes.
- If someone refreshes the page during a pairing, they just open the same link again and tap their name.

## Privacy

- Nothing is written to disk or stored in a database.
- Uploads are held in the app's memory only for your session. When you pair or swipe across two phones, they're kept for up to 12 hours so the other phone can load them. Restarting the app clears everything.
- The app only reads your watched films, ratings, watchlist and profile name.

## Running it locally

You'll need Python 3.10 or newer.

```bash
git clone https://github.com/tysgreen/double-feature.git
cd double-feature
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

It opens in your browser. To try it on a phone on the same Wi-Fi, use the **Network URL** printed in the terminal.

## How it works

- **One file:** the whole app is in `app.py`, built with [Streamlit](https://streamlit.io) (1.64+), pandas and Altair.
- **Matching:** films are matched across accounts by title and year. Anything you've rated counts as seen.
- **Taste match:** 100% minus the average rating gap on films you've both rated, scaled so a full 4.5★ gap would be 0%.
- **Posters:** the export has no images, so each film gets a generated screen-print-style poster based on its title.
- **Pairing and swiping:** rooms live in server memory (`st.cache_resource`) under a four-letter code. Both phones check for changes every couple of seconds. The swipe cards are a small custom component (`st.components.v2`) that supports dragging and buttons.
- **Theme and fonts:** set in `.streamlit/config.toml`. The fonts (Fraunces, DM Sans and Bebas Neue) are served from `static/`.

```
├── app.py
├── requirements.txt
├── .streamlit/
│   └── config.toml
└── static/
    ├── bebas-neue.woff2
    ├── dm-sans.woff2
    ├── fraunces.woff2
    └── fraunces-italic.woff2
```

## Limitations

- On Streamlit Community Cloud, the app goes to sleep after 12 hours without visitors. The first person back sees a "wake up" button and waits about a minute.
- Pairings and swipe sessions last up to 12 hours and are lost if the app restarts, for example after an update.
- Matching on title and year can occasionally mix up two films with the same name and year.

---

Not affiliated with or endorsed by Letterboxd. Film data comes from your own Letterboxd export.
