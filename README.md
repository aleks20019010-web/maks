# РњР°РєСЃ вЂ” РіРѕР»РѕСЃРѕРІРѕР№ РґРІРѕСЂРµС†РєРёР№

Р“РѕР»РѕСЃРѕРІРѕР№ Р°СЃСЃРёСЃС‚РµРЅС‚ РЅР° СЂСѓСЃСЃРєРѕРј. РњСѓР·С‹РєР°, РїРѕРіРѕРґР°, СЃС‚Р°С‚СѓСЃ СЃРёСЃС‚РµРјС‹, РЅР°РїРѕРјРёРЅР°РЅРёСЏ, СЂРёС‚СѓР°Р»С‹, РѕС‡РёСЃС‚РєР° РґРёСЃРєР°.

## РўСЂРµР±РѕРІР°РЅРёСЏ

- Windows 10/11
- Python 3.10+
- VLC
- Vosk-РјРѕРґРµР»СЊ vosk-model-small-ru-0.22 РІ models/
- РўРѕРєРµРЅ РЇРЅРґРµРєСЃР° РІ config/yandex_key.txt

## РЈСЃС‚Р°РЅРѕРІРєР°

1. git clone https://github.com/aleks20019010-web/maks.git
2. cd maks
3. pip install -r requirements.txt
4. winget install VideoLAN.VLC
5. РЎРєР°С‡Р°С‚СЊ Vosk-РјРѕРґРµР»СЊ РІ models/
6. РЎРѕР·РґР°С‚СЊ config/secrets.json СЃ С‚РѕРєРµРЅР°РјРё
7. Р—Р°РїСѓСЃС‚РёС‚СЊ start.bat

## РЎС‚СЂСѓРєС‚СѓСЂР°

- brain/ вЂ” СЏРґСЂРѕ (router, skills, composer)
- personality_core/ вЂ” Р»РёС‡РЅРѕСЃС‚СЊ (engine, prosody, style, self_state)
- memory/ вЂ” РїР°РјСЏС‚СЊ
- workers/ вЂ” asr, tts, music
- shared/ вЂ” config, protocol, ipc, log
- responses/ вЂ” JSON С„СЂР°Р·С‹
- config/ вЂ” РЅР°СЃС‚СЂРѕР№РєРё
- docs/ вЂ” РґРѕРєСѓРјРµРЅС‚Р°С†РёСЏ
- tools/ вЂ” СѓС‚РёР»РёС‚С‹

## Р›РёС†РµРЅР·РёСЏ

MIT
