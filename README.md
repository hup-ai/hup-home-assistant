# Hup for Home Assistant

Connect any Home Assistant camera to [Hup](https://withhup.com) for AI-powered home monitoring and voice interaction.

## Installation

### HACS (Recommended)

1. Open HACS → **Integrations** → three-dot menu → **Custom repositories**
2. Add `https://github.com/hup-ai/hup-home-assistant` as an **Integration**
3. Search for "Hup" and install
4. Restart Home Assistant

### Manual

Copy the `custom_components/hup` folder into your Home Assistant `config/custom_components/` directory, then restart.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Hup**
3. Enter your API key (from the Hup app under **Settings → External Devices**)
4. Select a camera and snapshot interval
5. Done — snapshots upload automatically

To monitor multiple cameras, add the integration again for each one.
