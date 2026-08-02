# Vendored fonts

Both are self-hosted rather than loaded from a CDN, so the UI renders identically
offline and makes no third-party requests at runtime.

| File | Family | Version | License |
|---|---|---|---|
| `inter.woff2` | [Inter](https://github.com/rsms/inter) (variable, latin, weight axis 100–900) | via `@fontsource-variable/inter` | [SIL Open Font License 1.1](https://openfontlicense.org) |
| `jetbrains-mono.woff2` | [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) (variable, latin, weight axis 100–800) | via `@fontsource-variable/jetbrains-mono` | [SIL Open Font License 1.1](https://openfontlicense.org) |

The OFL permits bundling and redistribution with this kind of project. Neither
file has been modified or renamed as a font family.

To refresh them:

```bash
curl -sfL -o ui/fonts/inter.woff2 "https://cdn.jsdelivr.net/npm/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2"
curl -sfL -o ui/fonts/jetbrains-mono.woff2 "https://cdn.jsdelivr.net/npm/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2"
```
