# AutoExperten Logo-Dateien

Hier gehören die **offiziellen** Logo-Dateien hin (bitte kein neues Logo erstellen):

| Datei                         | Verwendung                                      |
| ----------------------------- | ----------------------------------------------- |
| `autoexperten-logo.svg`       | Helle Hintergründe ("Auto" dunkel, "Experten" blau) |
| `autoexperten-logo-light.svg` | Dunkle Hintergründe ("Auto" hell, "Experten" blau)  |

Solange die Dateien fehlen, zeigt `src/components/brand/BrandLogo.tsx` einen
Text-Platzhalter. Nach dem Ablegen der Dateien in
`src/config/brand.ts` → `LOGO_ASSETS.useAssetFiles = true` setzen.
Die UI muss dafür nicht geändert werden.

Die App-Icons in `/public/icons` sind ebenfalls Platzhalter ("AE"-Monogramm)
und sollten aus dem offiziellen Logo abgeleitet werden.
