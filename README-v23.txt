MAÇ ANALİZ PRO v23 — YEREL LİG GEÇMİŞİ

v22 üzerine güncellemedir. GitHub'a ZIP içindeki dosyaları aynı klasörlere yükleyin.
Mevcut match_archive.sqlite3, data.json ve prediction_history.json dosyalarını silmeyin.

Yeni: Avrupa kupalarında karşılaşılan bazı kulüpler için doğrulanmış yerel lig eşleştirmeleri.
Takımın Avrupa kupası ESPN kimliği yerel ligde yeniden kullanılmaz; yerel ligin skor tablolarındaki takım adı üzerinden eşleştirilir.
En fazla 120 günlük geçmiş 14 günlük pencerelerle sorgulanır. Bulunan tamamlanmış maçlar mevcut arşive eklenir.
Aynı çalışmada ortak lig/tarih sorguları önbellekten kullanılır.

Log: TEAM ... domestic=...; domestic matches=...; failures=...
"domestic=unmapped" henüz yerel lig eşleştirmesi olmayan takımdır.
ESPN bazı yerel ligleri desteklemeyebilir; bu durumda mevcut arşiv korunur ve hata sayısı loglanır.
Bu sürüm, bütün dünya kulüplerini otomatik tanıdığını iddia etmez. Son 10 maç hedefi veri kaynağı kapsamına bağlıdır.

Test: Python sözdizimi ve JavaScript sözdizimi. Canlı ESPN sorguları bu ortamda doğrulanmadı.
