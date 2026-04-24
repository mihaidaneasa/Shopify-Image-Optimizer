🛍️ Shopify Image Optimizer (WebP Automation)

Script Python pentru optimizarea automată a imaginilor produselor din Shopify folosind GraphQL Bulk API.

🚀 Ce face acest script
🔍 Detectează toate imaginile produselor:
imagini principale
imagini din variante
imagini din descriere (HTML)
⚡ Convertește imaginile în format WebP
📤 Încarcă automat imaginile optimizate în Shopify
🧹 Șterge imaginile vechi (non-WebP) după înlocuire
💾 Salvează copii locale ale imaginilor optimizate
🛑 Include interfață cu buton STOP
⚙️ Funcționalități principale
✔️ Bulk processing (rapid și eficient)
✔️ Skip automat pentru imaginile deja .webp
✔️ Calitate configurabilă (default: 92)
✔️ Moduri de rulare:
DRY-RUN (simulare)
LIVE (modificări reale în Shopify)
✔️ Interfață simplă (popup + control window)
✔️ Sistem de retry pentru request-uri API
✔️ Compatibil cu .exe (PyInstaller)
📦 Cerințe
Python 3.9+
Acces la Shopify Admin API
🔧 Instalare

Instalează dependențele:

pip install requests pillow python-dotenv
🔐 Configurare

Creează un fișier .env în același folder:

SHOP_DOMAIN=magazinul-tau.myshopify.com
ADMIN_TOKEN=shpat_xxxxxxxxxxxxxxxxx

⚠️ IMPORTANT:

Nu urca .env pe GitHub
Nu expune token-ul Shopify
▶️ Rulare
python optimizare_imagini_shopify.py
🧪 Moduri de rulare

La pornire vei primi o confirmare:

✅ YES → rulează în mod LIVE (modifică Shopify)
❌ NO → rulează în mod DRY-RUN (simulare fără modificări)
📁 Output

Imaginile convertite sunt salvate local în:

out_images/

sau într-un folder ales prin interfață (GUI).

🎛️ Parametri disponibili
Parametru	Descriere
--limit	Limitează numărul de imagini procesate
--quality	Calitate WebP (60–100)
--out	Folder output
--dry-run	Simulare fără modificări
--skip-webp	Sare imaginile deja WebP (implicit activ)
--no-skip-webp	Procesează și WebP
--stem-filter	Păstrează o singură imagine per nume
--dedup-url	Elimină duplicatele după URL
🛑 Control & Siguranță
Fereastră dedicată cu buton STOP
Oprire sigură în timpul rulării
Sistem de retry pentru erori de rețea
Validare și tratare erori imagini
⚠️ Atenționări
Testează întotdeauna în DRY-RUN înainte de LIVE
Nu rula pe producție fără backup
Shopify API are limitări (rate limit)
🔒 Securitate

Scriptul suportă:

.env pentru dezvoltare
env.dpapi pentru producție (criptat)

Nu include fișiere sensibile în repository!

📌 Structură recomandată
shopify-image-optimizer/
│
├── optimizare_imagini_shopify.py
├── README.md
└── .gitignore
💡 Utilizare recomandată
Optimizare SEO (viteza paginii)
Reducere costuri bandwidth
Automatizare pentru magazine mari
👨‍💻 Autor

Script dezvoltat pentru automatizarea procesării imaginilor Shopify.
Script dezvoltat folosind AI.

⭐ Contribuții

Sugestiile și îmbunătățirile sunt binevenite!
