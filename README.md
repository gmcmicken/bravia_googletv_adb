# Bravia Google TV ADB Playback

Adds a media player that reports `playing` / `paused` / `idle` for the foreground app (e.g. Netflix) on an Android TV, for TVs where the built-in Android Debug Bridge integration can't detect the app.

It reuses the ADB connection of an existing Android Debug Bridge entry, so no second connection is opened.

## Install

1. HACS → Custom repositories → add this repository as an **Integration**.
2. Install, restart Home Assistant.
3. Settings → Devices & services → Add integration → **Bravia Google TV ADB Playback**, then choose your Android Debug Bridge media player.
