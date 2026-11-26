# Riset Sumber Arsip Sejarah & Footage Domain Publik (V102-D v2)

- **Tanggal riset:** 2026-11-26 (WIB / UTC+07:00)
- **Lingkup:** Evaluasi enam kandidat API arsip sejarah untuk kebutuhan konten folklore, sejarah kelam, dokumenter, serta **footage video arsip** di EasyFaceless Studio (`stage/v102`). Tanpa implementasi kode di `app/` (tahap riset & arsitektur D2).
- **Aturan pembuktian:** Seluruh klaim API, kuota, ToS, dan lisensi merujuk sumber resmi (URL + kutipan verbatim ≤15 kata + waktu akses) atau hasil uji HTTP langsung; hal yang terblokir WAF/timeout/kunci ditandai **tidak terverifikasi**.

---

## Ringkasan Cepat & Rekomendasi

| # | Kandidat | Media | Kunci API | ToS Komersial | Filter Server Aman | Akses HTTP Langsung (`2026-11-26`) | Status Rekomendasi |
|---:|---|---|---|---|---|---|---|
| 1 | **Internet Archive (Video/Prelinger & Gambar)** | **Video MP4** + Gambar | **Tidak perlu** (Read/Search) | Tergantung item (`licenseurl` + `rights`) | Ya (daftar URI dikutip di `q=`) | **HTTP 200 + HTTP 206 Range** | **Rekomendasi #1 (Utama — Video)** |
| 2 | **Smithsonian Open Access** | Gambar res tinggi *(video terbatas non-CC0)* | **Ya (gratis `api.data.gov` / `DEMO_KEY`)** | **CC0** (`usage.access == "CC0"`) | `online_media_type:Images` *(CC0 dasar)* | **HTTP 200** (`DEMO_KEY`) | **Rekomendasi #2 (Utama — Gambar BYOK)** |
| 3 | **Europeana Search API v2** | Gambar + Video/Audio GLAM Eropa | **Ya (gratis `wskey`)** | Tidak terverifikasi langsung *(docs 403 WAF)* | Ya (`reusability=open` — perlu verifikasi) | API endpoint aktif (`401` tanpa key); docs `403` | **Kandidat #3 Bersyarat** *(verifikasi ToS & key dulu)* |
| 4 | **Library of Congress (`loc.gov`)** | Foto/Cetak/Video historis | Tidak perlu | User tanggung hak item (`legal/`) | Tidak ada facet lisensi PD | **Diblokir Cloudflare `403` (`Just a moment...`)** | **Tidak direkomendasikan saat ini** |
| 5 | **Flickr Commons / API** | Foto arsip institusi | Ya (App Garden) | **Dilarang non-komersial tanpa izin khusus SmugMug** | Ya (`license=4,5,7,8,9,10,11,12`) | `200` (API mengembalikan `code 100` tanpa key) | **Ditolak (ToS non-komersial)** |
| 6 | **NYPL Digital Collections** | Foto/Cetak/Video | Ya (token email) | Docs ToS penuh `www.nypl.org` timeout | Ya (`publicDomainOnly=true`) | **API resmi dideprekasi per 1 Agu 2026** | **Ditolak (Deprecated)** |

---

## 1. NYPL Digital Collections API

### 1.1 Endpoint, Autentikasi & Uji Permintaan
- **Status Kritis — DEPREKASI RESMI:** Halaman resmi API NYPL ([https://api.repo.nypl.org/](https://api.repo.nypl.org/), HTTP `200`, akses `2026-11-26 17:58:08 +0700`):
  > *"The Repo API is being deprecated and will no longer be available starting August"* (14 kata — lanjutan: *"will no longer be available starting August 1st, 2026."*, 9 kata)
  > *"No public API replacement is planned at this time."* (9 kata)
- **Endpoint pencarian & hak:** `GET https://api.repo.nypl.org/api/v2/items/search?q={query}&publicDomainOnly=true` serta `GET /api/v2/items/item_details/{uuid}` dan `GET /api/v2/items/rights/{uuid}`.
- **Autentikasi:** Header `-H 'Authorization: Token token="<token>"'` atau HTTP Basic (`https://api.repo.nypl.org/`, akses `2026-11-26 17:58:08 +0700`).
- **Uji tanpa token** (`https://api.repo.nypl.org/api/v2/items/search?q=medieval+plague`, akses `2026-11-26 17:59:21 +0700`): **HTTP `401`**, respons:
```text
HTTP Token: Access denied.
```

### 1.2 Kuota, Rate Limit & ToS Distribusi Aplikasi
- **Kuota resmi:** Dari [https://api.repo.nypl.org/](https://api.repo.nypl.org/) (akses `2026-11-26 17:58:08 +0700`):
  > *"connecting from an application or curl request is 10,000 requests per day."* (12 kata)
- **ToS penggunaan komersial dalam aplikasi terdistribusi:** Halaman hukum lengkap ([https://www.nypl.org/help/about-nypl/legal-notices/website-terms-and-conditions](https://www.nypl.org/help/about-nypl/legal-notices/website-terms-and-conditions)) mengalami `ConnectTimeout` pada `2026-11-26 17:58:13 +0700`, dan `https://api.repo.nypl.org/terms` mengembalikan HTTP `200` berpesan API error 400 (`"The path that you entered is invalid"`), sehingga **klausul ToS komersial penuh NYPL tidak terverifikasi langsung** dari situs `www.nypl.org`.

### 1.3 Lisensi, Filter Server, Atribusi & Media
- **Filter server:** Parameter `publicDomainOnly=true` ([https://api.repo.nypl.org/](https://api.repo.nypl.org/), akses `2026-11-26 17:58:08 +0700`): *"returns only Public Domain results"* (5 kata).
- **Field respons hak:** `rightsStatement`, `rightsStatementURI` (pada `/items/recent`), serta `useStatement`, `rightsNotes` (pada `/items/rights/{uuid}`). Tersedia empat kategori hak resmi (`No Known U.S. Copyright`, `CC0`, `Copyright Held or Managed by NYPL`, `Undetermined`); item di luar `No Known U.S. Copyright` tidak diberi tautan unduh langsung.
- **Atribusi (`credits.md`):** Opsional (*"Though not required"*), NYPL meminta kutipan:
  > *"use the following statement, "From The New York Public Library,""* (10 kata, [https://api.repo.nypl.org/](https://api.repo.nypl.org/), akses `2026-11-26 17:58:08 +0700`).
- **Resolusi & format media:** Turunan `imageLinks/imageLink` kode `q` (`1600px`), `v` (`2560px`), dan `g` (`original dimensions`, `>= 1280px` langsung tanpa IIIF wajib), plus `highResLink` (TIFF >200 MB). `typeOfResource` mencantumkan `"moving image"`, tetapi ketersediaan URL unduh langsung video lewat API v2 **tidak terverifikasi tanpa token**.

### 1.4 Uji 3 Query Folklore/Sejarah Gelap & Risiko
- **Hasil 3 query:** Tidak dapat dijalankan tanpa token aktif (**tidak terverifikasi tanpa token**, dan API dideprekasi 1 Agustus 2026).
- **Risiko:** Risiko absolut **shutdown layanan** sesuai pengumuman resmi *"no longer be available starting August 1st, 2026"*, serta ketergantungan token pribadi/pengembang.

---

## 2. Library of Congress JSON API (`loc.gov`)

### 2.1 Endpoint, Autentikasi & Uji Permintaan
- **Autentikasi:** Tanpa API key ([https://www.loc.gov/apis/json-and-yaml/](https://www.loc.gov/apis/json-and-yaml/), HTTP `200`, akses `2026-11-26 17:58:29 +0700`):
  > *"The API is accessible to the public with no API key or authentication required."* (14 kata)
- **Endpoint resmi:** `GET https://www.loc.gov/{endpoint}/?fo=json` (`/search/`, `/photos/`, `/collections/`, `/item/{id}/`), parameter `q=`, `fa=` (facet), `c=` (per page, default 25), `sp=` (page), `at=` (seleksi atribut) ([https://www.loc.gov/apis/json-and-yaml/requests/parameters/](https://www.loc.gov/apis/json-and-yaml/requests/parameters/), HTTP `200`, akses `2026-11-26 18:05:06 +0700`).
- **Contoh query (tidak dieksekusi massal):**
  `GET https://www.loc.gov/photos/?q=medieval+plague&fo=json&c=5`
- **Hasil uji HTTP langsung (Blocker Cloudflare!):** Lima kali uji sopan (interval ≥3,2 detik) terhadap `/search/?fo=json`, `/photos/?fo=json`, dan `/pictures/search/?fo=json` — baik dengan `User-Agent` bot maupun Chrome `Mozilla/5.0` — **semuanya dibalas HTTP `403` tantangan Cloudflare** (`2026-11-26 17:58:52` s/d `18:00:40 +0700`):
```html
HTTP 403 | Content-Type: text/html; charset=UTF-8
<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"><meta http-equiv="X-UA-Compatible" content="IE=Edge"><meta name="robots" content="noindex,nofollow">
```

### 2.2 Kuota, Rate Limit & ToS Distribusi Aplikasi
- **Pedoman rate limit resmi:** Halaman [https://www.loc.gov/legal/](https://www.loc.gov/legal/) (HTTP `200`, akses `2026-11-26 17:58:33 +0700`):
  > *"submit a total of no more than 10 requests per minute to our applications"* (14 kata)
  - Halaman [https://www.loc.gov/apis/json-and-yaml/](https://www.loc.gov/apis/json-and-yaml/) (akses `2026-11-26 17:58:29 +0700`) membatasi deep paging:
    > *"Paging past the 100,000th item in a search result is not supported"* (12 kata)
  - Halaman `/working-within-limits/` mengalami `ReadTimeout` (`2026-11-26 18:02:32 +0700`), sehingga batas per-microservice (mis. `20 req/min` vs `10 req/min` di `/legal/`) **tidak terverifikasi langsung** dari subhalaman tersebut.
- **ToS hak cipta & distribusi:** Dari [https://www.loc.gov/legal/](https://www.loc.gov/legal/) (akses `2026-11-26 17:58:33 +0700`):
  > *"we generally do not own the rights to materials in our collections."* (12 kata)
  > *"determine for yourself whether or not an item is protected by copyright"* (12 kata)

### 2.3 Lisensi, Filter Server, Atribusi & Media
- **Filter server:** Facet yang terdokumentasi ([https://www.loc.gov/apis/json-and-yaml/responses/search-results/](https://www.loc.gov/apis/json-and-yaml/responses/search-results/), HTTP `200`, akses `2026-11-26 18:03:30 +0700`) hanya mencakup `access-restricted`, `digitized`, `online-format`, `original-format`, `partof`, `subject` — **tidak ada facet filter lisensi PD/CC0 di sisi server**.
- **Field hak per item & ukuran gambar:** Pada `/apis/json-and-yaml/` (`2026-11-26 17:58:29 +0700`) disebutkan *"images (thumbnails and higher resolution formats)"* dan `image_url` di atribut hasil, namun struktur aktual field `rights` / `rights_advisory` dan ukuran piksel pada respons JSON **tidak terverifikasi via HTTP langsung** karena seluruh endpoint JSON tertahan `HTTP 403` Cloudflare.
- **Atribusi (`credits.md`):** Tanggung jawab pemeriksaan hak & atribusi diserahkan ke pengguna (`https://www.loc.gov/legal/`).

### 2.4 Uji 3 Query Folklore/Sejarah Gelap & Risiko
- **`yuki-onna woodblock`:** `HTTP 403` (Cloudflare `"Just a moment..."`, `2026-11-26 17:58:52 +0700`).
- **`medieval plague`:** `HTTP 403` (Cloudflare `"Just a moment..."`, `2026-11-26 17:58:55` & `18:00:30 +0700`).
- **`ghost folklore illustration`:** `HTTP 403` (Cloudflare `"Just a moment..."`, `2026-11-26 17:58:59 +0700`).
- **Risiko:** Risiko sangat tinggi bagi aplikasi desktop berbasis Python `requests`: IP pengguna rumahan langsung memicu Cloudflare bot-challenge (`403`), membuat pipeline otomatis terhenti atau masuk cooldown B1/B3 (`Just a moment` sudah memicu cooldown di `resilient_http_json_get`).

---

## 3. Europeana Search API v2

### 3.1 Endpoint, Autentikasi & Uji Permintaan
- **Endpoint pencarian & rekor:** `GET https://api.europeana.eu/record/v2/search.json?wskey={WSKEY}&query={QUERY}&reusability=open` *(rekonstruksi query wajib disertai catatan: halaman dokumentasi utama `https://pro.europeana.eu/page/apis` & `https://pro.europeana.eu/page/search` membalas **HTTP `403`** saat diakses via `requests` pada `2026-11-26 17:58:34 +0700`; parameter `wskey` dan `reusability=open` dikonfirmasi melalui hasil penelusuran terindeks `europeana.eu`/`atlassian.net` pada `18:02:01 +0700`)*.
- **Uji endpoint langsung tanpa kunci** (`https://api.europeana.eu/record/v2/search.json?query=medieval+plague`, akses `2026-11-26 17:59:22 +0700`): **HTTP `401`** *(menandakan endpoint API sendiri TIDAK diblokir WAF dan aktif merespons JSON)*:
```json
{
  "success": false,
  "error": "Unauthorized",
  "message": "Invalid API key provided!",
  "code": "invalid_apikey"
}
```

### 3.2 Kuota, Rate Limit & ToS Distribusi Aplikasi
- **Status verifikasi dokumen resmi:** Empat URL dokumentasi dan ToS (`https://pro.europeana.eu/page/apis`, `https://pro.europeana.eu/page/search`, `https://www.europeana.eu/en/rights/api-terms-of-use`, `https://www.europeana.eu/en/rights/rr-f`) mengembalikan **HTTP `403` WAF** pada `2026-11-26 17:58:34–36 +0700`, dan Wiki Confluence Atlassian (`https://europeana.atlassian.net/wiki/spaces/EF/pages/2091155489/Search+API`) hanya memuat cangkang JS (`1.057 chars`).
- **Akibatnya:** Angka kuota numerik resmi (req/detik atau harian untuk Personal Key vs Project Key) serta teks klausul distribusi aplikasi pada `api-terms-of-use` **tidak terverifikasi secara verbatim** dari halaman resmi dalam sesi ini.

### 3.3 Lisensi, Filter Server, Atribusi & Media
- Dari ringkasan dokumentasi resmi `europeana.eu` terindeks (`2026-11-26 18:05:14` & `18:06:18 +0700`), Europeana menggunakan field **`edm:rights`** dan filter parameter **`reusability=open`** (mengelompokkan CC0, Public Domain Mark, CC BY, CC BY-SA), sementara metadata bersifat CC0. Namun **kutipan verbatim ≤15 kata langsung dari HTML resmi `pro.europeana.eu` tidak tersedia** karena `HTTP 403`.
- Demikian pula ketersediaan langsung file gambar `>= 1280px` atau video vs pratinjau `edmPreview` / IIIF pada respons Search API v2 **tidak terverifikasi** tanpa `wskey`.

### 3.4 Uji 3 Query Folklore/Sejarah Gelap & Risiko
- **Hasil 3 query:** Tidak dapat dijalankan tanpa `wskey` (**HTTP `401 invalid_apikey`**).
- **Risiko:** Memerlukan BYOK pengguna atau `Project Key` tersemat (*embed key risk* pada binary terdistribusi), plus portal dokumentasi memblokir client `requests` (`403`) walaupun endpoint `api.europeana.eu` aktif (`401`). Perlu verifikasi manual halaman ToS & pengujian dengan kunci sebelum dipromosikan ke jalur default.

---

## 4. Smithsonian Open Access API (`api.si.edu`)

### 4.1 Endpoint, Autentikasi & Uji Permintaan
- **Endpoint pencarian:** `GET https://api.si.edu/openaccess/api/v1.0/search?q={query}&rows={n}&api_key={KEY}`
- **Autentikasi (`api.data.gov`):** Memerlukan parameter `api_key=` atau header `X-Api-Key` yang diperoleh gratis melalui `api.data.gov`. Mendukung `DEMO_KEY` terverifikasi langsung pada `api.si.edu`!
- **Uji tanpa key** (`https://api.si.edu/openaccess/api/v1.0/search?q=medieval+plague`, akses `2026-11-26 17:59:24 +0700`): **HTTP `403`**:
```json
{
  "error": {
    "code": "API_KEY_MISSING",
    "message": "No api_key was supplied. Get one at https://api.si.edu:443"
  }
}
```
- **Uji nyata HTTP `200` dengan `DEMO_KEY` & filter media gambar** (`q=(ghost folklore illustration) AND online_media_type:Images`, akses `2026-11-26 18:06:07 +0700`, `X-RateLimit-Remaining: 4`):
```json
{
  "id": "ld1-1643399887910-1643399888294-0",
  "title": "Illustration for Hamlet: Hamlet Tries to Follow the Ghost of his Father (I,4)",
  "unitCode": "CHNDM",
  "data_source": "Cooper Hewitt, Smithsonian Design Museum",
  "metadata_usage": { "access": "CC0" },
  "media": [{
    "type": "Images",
    "usage": { "access": "CC0" },
    "width": 2597, "height": 3520,
    "content": "https://ids.si.edu/ids/deliveryService?id=CHSDM-BDD4E32E62C72-000001",
    "resources": [{
      "label": "High-resolution JPEG",
      "url": "https://ids.si.edu/ids/download?id=CHSDM-BDD4E32E62C72-000001.jpg",
      "width": 2810, "height": 3809
    }]
  }]
}
```

### 4.2 Kuota, Rate Limit & ToS Distribusi Aplikasi
- **Header kuota nyata `DEMO_KEY`:** Respons `api.si.edu` (`2026-11-26 18:05:31 +0700`) mengembalikan header `X-RateLimit-Limit: 10` *(terukur langsung untuk burst DEMO_KEY; penelusuran `site:api.data.gov`, `2026-11-26 18:05:18 +0700`, mendokumentasikan `DEMO_KEY` dibatasi 30 req/jam dan kunci terdaftar gratis mendapat default **`1,000 requests per hour`**)*. Catatan: URL statis `https://api.data.gov/docs/rate-limits/` (`200`, `17:58:39 +0700`) merender via JS sehingga angka `1,000` tidak muncul dalam HTML mentah.
- **ToS resmi (`www.si.edu`):** Halaman `https://www.si.edu/termsofuse` & `https://www.si.edu/openaccess/devtools` mengembalikan **HTTP `403`** pada `2026-11-26 17:58:36–39 +0700`, sehingga teks ToS web penuh `si.edu/termsofuse` **tidak terverifikasi langsung**; namun status bebas hak cipta **CC0** melekat langsung pada metadata API (`"usage": {"access": "CC0"}`).

### 4.3 Lisensi, Filter Server, Atribusi & Media
- **Cara baca lisensi per item (Krusial!):** Jangan hanya memeriksa `content.descriptiveNonRepeating.metadata_usage.access`. Pada uji nyata `online_media_type:"Video recordings"` (`129` total item di Smithsonian, akses `2026-11-26 18:07:22 +0700`), sampel `nmaahc_2016.31.3.22.1abc` mengembalikan `metadata_usage.access: "CC0"` **tetapi** `indexedStructured.online_media_rights: ["Limited Use"]` dan `freetext.objectRights: "© Michael Holman"` / `"Permission required for use."`! Implementasi wajib mengecek **`media[i].usage.access == "CC0"`** (untuk gambar) dan menolak bila `online_media_rights` tidak mengizinkan CC0.
- **Filter server:** Gunakan `online_media_type:Images` di `q=` agar catatan perpustakaan tanpa media (`media: []`, seperti 19 hit awal `SIL` di query polos) tersaring di server.
- **Atribusi:** CC0 secara hukum tidak mewajibkan atribusi, namun disarankan mencantumkan `data_source` museum kontributor + `record_link` di `credits.md`.
- **Resolusi & format media:** Tersedia JPEG resolusi tinggi **langsung tanpa IIIF** melalui `resources[].url` (`High-resolution JPEG`, mis. `2810x3809` px, jauh melampaui batas `>= 1280px`) atau `media[].content`. **Tidak layak untuk video** karena hanya ~129 rekaman video terindeks dan dibatasi `Limited Use` berhak cipta.

### 4.4 Uji 3 Query Folklore/Sejarah Gelap & Risiko
Uji langsung via `api.si.edu/openaccess/api/v1.0/search` (`DEMO_KEY`, `2026-11-26 18:05:31–18:06:07 +0700`):
- **`yuki-onna woodblock`:** `0` hasil (baik polos maupun `+online_media_type:Images`).
- **`medieval plague`:** `19` polos (`media: []`) → **`225`** hasil dengan `(...) AND online_media_type:Images`.
- **`ghost folklore illustration`:** `44` polos → **`37,421`** hasil dengan `(...) AND online_media_type:Images` *(operator default antar-kata dalam kurung di EDAN bertindak longgar/OR sehingga `illustration` sangat meluaskan hasil, dengan contoh langsung relevan `CHSDM` `2810x3809 px`, `CC0`)*.
- **Risiko:** Memerlukan `api_key` `api.data.gov` (BYOK pengguna di *Settings → API & Penyedia AI* — satu key terpusat milik developer yang di-hardcode di aplikasi desktop member berisiko cepat kehabisan kuota `1,000/jam` secara kolektif!).

---

## 5. Flickr Commons & Flickr API

### 5.1 Endpoint, Autentikasi & Uji Permintaan
- **Endpoint pencarian:** `GET https://www.flickr.com/services/rest/?method=flickr.photos.search&api_key={KEY}&text={QUERY}&license=4,5,7,8,9,10,11,12&extras=license,url_o,url_l,url_k,owner_name&format=json&nojsoncallback=1` ([https://www.flickr.com/services/api/flickr.photos.search.html](https://www.flickr.com/services/api/flickr.photos.search.html), HTTP `200`, akses `2026-11-26 17:58:42 +0700`).
- **Autentikasi:** `api_key` **wajib**: *"Your API application key."* (4 kata).
- **Uji tanpa key** (`2026-11-26 17:59:25 +0700`): **HTTP `200`** berbadan error JSON:
```json
{"stat": "fail", "code": 100, "message": "Invalid API Key (Key has invalid format)"}
```

### 5.2 Kuota, Rate Limit & ToS Komersial (Blocker Utama!)
- **Kuota numerik resmi:** **Tidak terverifikasi** angka per-jam di halaman ToS/API resmi (`flickr.com/help/terms/api` hanya melarang `"use an unreasonable amount of bandwidth"` dan membatasi `"Display more than 30 Flickr user photos per page"`).
- **Blocker ToS Komersial:**
  - Dari [https://www.flickr.com/services/api/](https://www.flickr.com/services/api/) (HTTP `200`, akses `2026-11-26 17:58:41 +0700`):
    > *"API Flickr tersedia untuk penggunaan non-komersial oleh pengembang di luar Flickr."* (11 kata)
  - Dari Flickr APIs Terms of Use ([https://www.flickr.com/help/terms/api](https://www.flickr.com/help/terms/api), HTTP `200`, akses `2026-11-26 17:58:43 +0700`):
    > *"If the primary purpose of your application is to derive revenue, it is considered"* (14 kata — *"a commercial application"*).
    > *"Users are charged a fee for your product or service which includes some sort"* (14 kata — *"of integration using the Flickr APIs"*).
    > *"we may grant your request for a commercial API key subject to your payment"* (14 kata — *"of fees"*).
    - Jika memakai Flickr API di aplikasi berbayar/komersial EasyFaceless Studio, kunci standar non-komersial **melanggar ToS**, sedangkan kunci komersial butuh persetujuan manual SmugMug dan berpotensi dikenai biaya infrastruktur.

### 5.3 Lisensi, Filter Server, Atribusi & Media
- **Pemetaan `license` ID resmi** ([https://www.flickr.com/services/api/flickr.photos.licenses.getInfo.html](https://www.flickr.com/services/api/flickr.photos.licenses.getInfo.html), HTTP `200`, akses `2026-11-26 17:58:42 +0700`):
  - **Aman komersial:** `4` (CC BY 2.0), `5` (CC BY-SA 2.0), `7` (*No known copyright restrictions* — Flickr Commons), `8` (US Government Work), `9` (CC0), `10` (Public Domain Mark), `11` (CC BY 4.0), `12` (CC BY-SA 4.0). *(Semua NC/ND seperti `1,2,3,6,13,14,15,16` wajib ditolak).*
  - Dapat difilter langsung di server dengan `&license=...` dan/atau `&is_commons=true`.
- **Catatan risiko hukum Flickr Commons** ([https://www.flickr.com/commons/usage/](https://www.flickr.com/commons/usage/), HTTP `200`, akses `2026-11-26 18:05:08 +0700`):
  > *"INSTITUSI YANG BERPARTISIPASI BERBAGI MANFAAT DARI RISET MEREKA TANPA MEMBERIKAN JAMINAN YANG..."* (12 kata — risiko tanggung jawab tetap pada pengguna).
- **Atribusi (`credits.md`) & UI App:** Selain atribusi CC BY/BY-SA per foto, API ToS ([https://www.flickr.com/help/terms/api](https://www.flickr.com/help/terms/api), `2026-11-26 17:58:43 +0700`) mewajibkan pemberitahuan aplikasi:
  > *"This product uses the Flickr API but is not endorsed or certified by SmugMug, Inc."* (15 kata)
- **Resolusi & format:** Tautan langsung `https://live.staticflickr.com/{server-id}/{id}_{secret}_{suffix}.jpg` ([https://www.flickr.com/services/api/misc.urls.html](https://www.flickr.com/services/api/misc.urls.html), `2026-11-26 18:07:24 +0700`): `url_k` (`2048px`), `url_h` (`1600px`), `url_o` (asli, bisa dibatasi pemilik). Tidak ditujukan untuk video arsip bebas hak cipta yang terkurasi.

### 5.4 Uji 3 Query & Risiko
- **Hasil 3 query:** Tidak dapat dijalankan tanpa `api_key` valid (`code: 100 Invalid API Key`).
- **Risiko:** Risiko kepatuhan ToS tinggi (aplikasi komersial dilarang menggunakan non-commercial key), aturan cache *"cache or store ... other than for reasonable periods"*, serta aturan hapus foto privat dalam 24 jam.

---

## 6. Internet Archive (termasuk Prelinger Archives — Prioritas Video Arsip)

### 6.1 Endpoint, Autentikasi & Uji Permintaan
- **Autentikasi:** **Tidak memerlukan API key** untuk endpoint pencarian (`advancedsearch.php`), scraping read-only (`/services/search/v1/scrape`), metadata read (`/metadata/{id}`), dan unduhan/streaming (`/download/{id}/{file}`) ([https://archive.org/developers/metadata.html](https://archive.org/developers/metadata.html), HTTP `200`, akses `2026-11-26 17:58:46 +0700`).
- **Endpoint resmi:**
  - Pencarian: `GET https://archive.org/advancedsearch.php?q={lucene_q}&fl[]=identifier&fl[]=title&fl[]=licenseurl&fl[]=rights&fl[]=mediatype&fl[]=collection&rows=20&page=1&output=json`
  - Metadata & daftar berkas: `GET https://archive.org/metadata/{identifier}` ([https://archive.org/developers/md-read.html](https://archive.org/developers/md-read.html), HTTP `200`, akses `2026-11-26 18:03:46 +0700`): *"MDAPI will return the item’s metadata record in JSON format."* (10 kata).
  - Unduhan berkas / stream: `GET https://archive.org/download/{identifier}/{filename}`
- **Uji nyata HTTP `200`** (query Prelinger `collection:prelinger`, `numFound: 10575`, akses `2026-11-26 17:59:16 +0700`) + metadata `/metadata/SpeechSt1949` (`HTTP 200`):
```json
{
  "metadata": {
    "identifier": "SpeechSt1949",
    "title": "Speech: Stage Fright and What to Do About It",
    "creator": "Centron Productions",
    "licenseurl": "http://creativecommons.org/licenses/publicdomain/",
    "collection": "prelinger",
    "date": "1949"
  },
  "video_files_sample": [
    {"name": "SpeechSt1949.mp4", "format": "h.264", "size": "59802653", "length": "574.74", "width": "640", "height": "480"},
    {"name": "SpeechSt1949_512kb.mp4", "format": "512Kb MPEG4", "size": "40577023", "length": "574.74", "width": "320", "height": "240"}
  ]
}
```

### 6.2 Kuota, Rate Limit, User-Agent & ToS Aplikasi
- **Syarat User-Agent & Rate Limit Resmi:** Dari [https://archive.org/developers/bots.html](https://archive.org/developers/bots.html) (HTTP `200`, akses `2026-11-26 18:03:44 +0700`):
  > *"All automated requests to archive.org must include a descriptive User-Agent header"* (11 kata)
  > *"Add delays between requests for bulk operations"* (7 kata)
  - Serta [https://archive.org/developers/iarest.html](https://archive.org/developers/iarest.html) (HTTP `200`, akses `2026-11-26 18:05:02 +0700`):
    > *"Used to indicate the client is being rate-limited due to an excessive number of requests"* (14 kata — HTTP `429` + header `Retry-After`).
  - Angka batas RPS hard-coded untuk `advancedsearch.php` tidak disebutkan secara numerik ("tidak terverifikasi angka RPS spesifik"), sehingga aplikasi wajib menerapkan `_respect_rate_limit` + memantau HTTP `429` & `Retry-After`.
- **ToS umum (`/about/terms`):** Halaman web ([https://archive.org/about/terms](https://archive.org/about/terms), HTTP `200`, `2026-11-26 17:58:50 +0700`) merender aplikasi JavaScript (*"Javascript is required for this site"*), sehingga teks klausul ToS HTML penuh tidak terbaca statis; hak penggunaan berkas mengikuti metadata masing-masing item dan FAQ resmi Prelinger (`https://www.panix.com/~footage/prelarch.html`, direferensikan oleh `archive.org/metadata/prelinger/metadata`).

### 6.3 Kebutuhan Khusus Video: Lisensi, Filter Server, Format & Pemotongan Tanpa Unduh Penuh
1. **Pembacaan & Penyaringan Lisensi:**
   - Dari skema resmi [https://archive.org/developers/metadata-schema/index.html](https://archive.org/developers/metadata-schema/index.html) (HTTP `200`, akses `2026-11-26 18:03:50 +0700`):
     - `licenseurl`: *"URL of the selected license"* (5 kata), *"defined by: uploader"* (3 kata), `required: No`.
     - `rights`: *"Rights statement"* (2 kata), *"defined by: uploader"* (3 kata).
   - **Cara filter di server yang terbukti valid:** Jangan memakai wildcard tidak dikutip (`licenseurl:*...*` memicu error JSON `"[BACKEND_ERROR] Invalid or no response from Elasticsearch"`, `2026-11-26 18:00:48 +0700`). Wajib menyertakan daftar URI berlisensi aman **dengan tanda kutip penuh**, mis. `licenseurl:"http://creativecommons.org/licenses/publicdomain/" OR licenseurl:"https://creativecommons.org/publicdomain/zero/1.0/" OR licenseurl:"https://creativecommons.org/publicdomain/mark/1.0/" OR licenseurl:"https://creativecommons.org/licenses/by/4.0/" ...`.
   - **Temuan Kritis Prelinger & Metadata Uploader:**
     - Pada FAQ resmi Prelinger ([https://www.panix.com/~footage/prelarch.html](https://www.panix.com/~footage/prelarch.html), HTTP `200`, akses `2026-11-26 18:07:20 +0700`):
       > *"Many people believe that all Prelinger Archives films are in the public domain."* (13 kata — lanjutan kalimat: *"Some are. Many are not."*)
       > *"The Internet Archive does NOT furnish written license agreements nor make any warranties"* (13 kata)
     - Pada hasil pencarian uji `medieval plague`, item `western-tradition` mencantumkan **`licenseurl: "https://creativecommons.org/publicdomain/mark/1.0/"` sekaligus `rights: "Fair Use Notice"`**! Selain itu, koleksi `mirrortube`/`fringe`/`altcensored`/`deemphasize` berisi cerminan video YouTube yang sangat berisiko salah tag.
     - **Aturan Wajib di Klien (D2):** Tolak semua item bila (a) `licenseurl` kosong atau mengandung `-nc`/`-nd`, (b) `rights` mengandung `Fair Use`, `Copyright`, `All Rights Reserved`, atau `Permission`, atau (c) `collection` memuat tag `mirrortube`/`fringe`/`altcensored`/`deemphasize` tanpa koleksi institusi terkurasi (`prelinger`, `classic_tv`, `opensource_movies` yang bersih, `clevelandart`, dsb.).
2. **Atribusi (`credits.md`):**
   - Prelinger ([`prelarch.html`](https://www.panix.com/~footage/prelarch.html), `18:07:20 +0700`): *"we do not require that you do so."* (kredit opsional `Prelinger Archives and Internet Archive`), sedangkan item `CC BY` / `CC BY-SA` wajib atribusi `creator` + `identifier` URL di `credits.md`.
3. **Format Video & Resolusi Gambar:**
   - Video arsip menyediakan bermacam turunan dalam `files[]` (`h.264` / `MPEG4` MP4, `512Kb MPEG4`, `Ogg Video`, `HiRes MPEG4`, `ProRes` / `MPEG-2` pada pindai terbaru `prelarch.html` bitrate 12 Mbps). Gambar (`clevelandart-1924.430-illumination-excised`) tersedia JPEG `14.59 MB` (`>= 1280px` langsung) dan TIFF `192.16 MB`.
4. **Bolehkan Memotong Cuplikan & Cara Teknis Tanpa Unduh Berkas Raksasa:**
   - **Legalitas pemotongan:** Diizinkan oleh Public Domain (`CC-PDM`/`CC0`/`publicdomain`), `CC BY`, dan `CC BY-SA` *(hindari `ND`/NoDerivs karena ND melarang pemotongan/turunan)*.
   - **Solusi Teknis Teruji (HTTP `206 Range` + FFmpeg Input Seeking `-ss`):**
     - Uji `Range: bytes=0-1023` pada `https://archive.org/download/SpeechSt1949/SpeechSt1949.mp4` (`2026-11-26 17:59:18 +0700`) mengembalikan **`HTTP 206 Partial Content`** (`Content-Range: bytes 0-1023/59802653`).
     - Uji `ffmpeg -y -ss 12.0 -i https://archive.org/download/SpeechSt1949/SpeechSt1949_512kb.mp4 -t 3.0 -c:v copy -an clip.mp4` (`2026-11-26 18:01:31 +0700`) mengekstrak klip 3 detik dari film berdurasi `574,74 dtk` (`40,58 MB`) hanya dalam **`5,1 detik`** dengan berkas akhir **`265 KiB`** (`returncode=0`) tanpa mengunduh 40 MB penuh!
     - **Strategi produksi D2:** Dari `/metadata/{id}`, pilih satu file `MPEG4`/`h.264` `.mp4` resolusi memadai (prioritaskan `640x480`–`1920x1080`, hindari turunan ProRes/MPEG-2 multi-GB), hitung offset seek aman `start_s = min(length * 0.25, shot_idx * 8.0)` agar terhindar dari credit title awal/akhir, lalu potong `target_duration + 1.5s` via FFmpeg input-seeking HTTP langsung ke `GlobalMediaCache` (dengan `-reconnect 1 -reconnect_streamed 1`, fallback unduh berkas penuh hanya jika `size <= 25 MB`).

### 6.4 Uji 3 Query Folklore/Sejarah Gelap & Risiko
Uji langsung via `archive.org/advancedsearch.php` (`2026-11-26 18:03:54 – 18:04:13 +0700`):
- **`yuki-onna woodblock`:** `0` hasil (`movies` aman), `0` (`prelinger`), `0` (`image` aman). *(Kueri 2 kata spesifik Jepang terlalu ketat AND).*
- **`medieval plague`:** `77` video tanpa filter lisensi → **`5`** video dengan filter URL lisensi PD/CC *(1 di antaranya `western-tradition` wajib disaring klien karena `rights: "Fair Use Notice"`, 1 `youtube-oDz0OrRZOZ0` di koleksi `fringe/deemphasize` perlu filter kualitas)*, dan **`15`** gambar ber-CC0 (`clevelandart` dsb.). Di `collection:prelinger` tepat: `0` (sedangkan kueri 1-kata historis Prelinger + PD mengembalikan `'plague': 3`, `'ghost': 13`, `'japan': 66`).
- **`ghost folklore illustration`:** `1` video tanpa lisensi → `0` video aman, `0` gambar aman *(AND 3-kata ketat; kueri `1–2` kata atau `OR` di pembangun query lebih cocok untuk arsip film).*
- **Risiko:** Risiko terbesar bukanlah kuota atau kunci API, melainkan **kualitas metadata unggahan komunitas** (`rights: "Fair Use"` menyusup di bawah `licenseurl: PDM`, serta unggahan `mirrortube`/`fringe`). Risiko ini sepenuhnya dapat ditutup dengan allowlist/blocklist koleksi + filter `rights` + pemilih segmen pendek `h.264`.

---

## 7. Matriks Skor & Rekomendasi Akhir

Skor skala `1–5` (5 = terbaik; **bobot ekstra pada Ketersediaan Video Arsip & Bebas Key Aplikasi Member** sesuai V102-D v2):

| Kriteria | Internet Archive | Smithsonian Open Access | Europeana v2 | LoC (`loc.gov`) | Flickr Commons/API | NYPL v2 |
|---|---:|---:|---:|---:|---:|---:|
| **Ketersediaan Video Arsip** | **5** *(Prelinger + ribuan film MP4)* | **1** *(hanya ~129 video & banyak non-CC0)* | **3** *(ada AV Eropa, butuh verifikasi)* | **3** *(terblokir Cloudflare)* | **1** *(fokus foto)* | **2** *(non-verifikasi + deprecated)* |
| **Gambar Arsip Resolusi ≥1280px** | **4** *(Museum JPEG/TIFF)* | **5** *(`High-res JPEG` CC0 `>2000px`)* | **4** *(belum teruji HTTP langsung)* | **1** *(403 Cloudflare)* | **4** *(2048px, perlu key)* | **0** *(shutdown 2026)* |
| **Keamanan Lisensi Komersial & Filter** | **3.5** *(perlu filter URL + blok `rights`/spam)* | **5** *(eksplisit `usage.access: "CC0"`)* | **4** *(`reusability=open` / `edm:rights`)* | **2** *(no server PD filter)* | **1** *(ToS non-komersial)* | **2** *(ToS timeout + deprecated)* |
| **Kesiapan Key & Distribusi Desktop** | **5** *(0 API key)* | **3** *(butuh key `api.data.gov` per pengguna)* | **3** *(butuh `wskey`)* | **1** *(403 Turnstile)* | **1** *(wajib Commercial Key berbayar)* | **0** *(deprecated 1 Agu 2026)* |
| **Kesesuaian Niche Folklore / Gelap** | **4** *(kuat di historis/vintage B&W 1–2 kata)* | **4.5** *(kuat ilustrasi/museum, mis. `37.421` hit gambar)* | **4** *(kaya arsip Eropa)* | **4** *(katalognya kaya jika tak 403)* | **3** | **3** |
| **Total Skor (maks. 25)** | **21.5** | **18.5** | **18.0** *(docs 403)* | **11.0** | **10.0** | **7.0** |

### Keputusan Rekomendasi untuk `V102-D2`:
1. **`internet_archive` (Prioritas #1 — Wajib, Tanpa API Key, Video + Gambar):** Satu-satunya sumber arsip yang langsung menyediakan **footage video historis/ephemeral (Prelinger Archives, dsb.)** bebas API key, mendukung `HTTP 206 Range` untuk pemotongan klip MP4 secara stream (`~5 detik`, `~265 KiB`) tanpa mengunduh file beratus-ratus MB.
2. **`smithsonian` (Prioritas #2 — Wajib, Gambar Museum CC0 Res Tinggi, BYOK `api.data.gov`):** Sangat kuat mengisi aset ilustrasi & objek bersejarah berlisensi **CC0** (`>= 2000px` unduhan JPEG langsung), dengan kunci gratis `api.data.gov` per pengguna (`1.000 req/jam`).
3. **`europeana` *(Kandidat #3 Opsional / Gelombang Berikutnya)*:** Disimpan sebagai ekspansi lanjutan bila tim memverifikasi ToS aplikasi terdistribusi pada browser manusia dan menguji `wskey` + `reusability=open`.

---

## 8. Rancangan Singkat Implementasi (`V102-D2`)

1. **Nama penyedia & modul:**
   - Buat modul terpisah **`app/services/longform/visual/archive_providers.py`** (agar `free_providers.py` tetap ringkas dan logika HTTP Range video trimming Internet Archive terisolasi), dengan kelas:
     - `InternetArchiveVisualProvider(BaseVisualProvider)` (`name = "internet_archive"`, mendukung `stock_video` & `stock_image`).
     - `SmithsonianVisualProvider(BaseVisualProvider)` (`name = "smithsonian"`, `stock_image`, memakai `api.data.gov` key).
   - Impor lazy dari `VisualService.get_provider()` di `visual/service.py` (konsisten dengan pola `StockVisualProvider`), dan daftarkan `"internet_archive"` ke `KEYLESS_FREE_VISUAL_SOURCES` serta `DEFAULT_VISUAL_SOURCE_ORDER` *(di belakang `"wikimedia"`, `"nasa"`, `"openverse"` agar pencarian gambar umum tetap cepat, dan aktif otomatis pada preset dokumenter/sejarah/folklore atau ketika query butuh video arsip)*.
2. **Keikutsertaan dalam Mode Campur Terbaik (`mix_best`) & Prioritas (`priority`):**
   - Di `resolve_provider_waterfall()`: `"internet_archive"` ikut otomatis dalam daftar sumber gratis tanpa key; `"smithsonian"` ikut otomatis **hanya** apabila `smithsonian_api_keys` terisi di konfigurasi (`AppConfig`).
   - Karena kandidat IA bisa berupa `"stock_video"` maupun `"stock_image"`, skor relevansi bersama (`score_candidate_relevance` + penyesuaian `source_type`) membuat `mix_best` langsung menyeimbangkan klip video Prelinger vs foto CC0 dengan aturan `60% / 3-berturut` dan dedup `canonical_candidate_dedup_key`.
   - Untuk kueri 3+ kata pada Internet Archive, tier kueri pertama memakai 2 kata inti + `AND` (karena `yuki-onna woodblock` dan `ghost folklore illustration` memberi `0` hasil dengan AND 3 kata), dan selalu menyertakan filter koleksi terkurasi + `licenseurl:"..."` + penolakan `Fair Use` di klien.
3. **Cache 24 Jam, Pemutus Sirkuit (B1/B3), & Pemotongan Video Range:**
   - Kedua penyedia memakai `cached_json_get(...)` dan `resilient_http_json_get(...)` dari `visual/free_providers.py`, otomatis mendapat **cache disk JSON 24 jam** (`_SEARCH_CACHE_TTL_SECONDS = 86400.0`), `_respect_rate_limit(min_interval_sec=0.5)` dengan header `EASYFACELESS_USER_AGENT` deskriptif sesuai pedoman `bots.html`, serta **circuit breaker `429`/`403`/`5xx`/timeout** (`check_provider_cooldown`, `trip_provider_cooldown`).
   - Pada `InternetArchiveVisualProvider.fetch_asset()`: jika `candidate.source_type == "stock_video"` dan ukuran berkas `> 25 MB`, panggil FFmpeg `-ss {start_s} -i {mp4_url} -t {needed_dur + 1.5}` dengan flag reconnect sebelum caching ke `GlobalMediaCache` (dengan atomic `.tmp.` + `os.replace` C3).
4. **Kolom Key di Settings (`webui` / `AppConfig`):**
   - `InternetArchiveVisualProvider`: **Tidak butuh kolom key** (100% keyless, siap jalan untuk semua user baru!).
   - `SmithsonianVisualProvider`: **Membutuhkan 1 kolom opsional** (*Smithsonian / api.data.gov API Key* — `smithsonian_api_keys`, tersimpan aman via helper redact secret B2 di `sanitize_source_error`). **Jangan** menanamkan `DEMO_KEY` atau key developer statis untuk Smithsonian karena `DEMO_KEY` terukur `X-RateLimit-Limit: 10` (30/jam per IP) dan kunci `api.data.gov` berkuota per-kunci.
