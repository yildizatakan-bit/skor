MAÇ ANALİZ PRO v22 – TAKIM GEÇMİŞİ SORGU TEŞHİSİ VE YEDEK TARAMA

Bu ZIP, kullanıcının yüklediği v21 paketi üzerine hazırlanmıştır.
Dosyaları GitHub deposunda aynı konumlara yükleyin.
Mevcut match_archive.sqlite3, data.json, prediction_history.json dosyalarını SİLMEYİN.

Değişiklik: scripts/update_data.py
- ESPN takım schedule yanıtındaki ham events sayısı ve boş yanıtlarda üst düzey anahtarlar loglanır.
- 200 OK ama boş events dönen schedule sorguları için ilgili turnuvanın scoreboard geçmişi 30 günlük dilimler halinde en çok 180 gün taranır.
- Takım kimliği ilgili turnuva bağlamında karşılaştırılır.
- Bulunan maçlar mevcut veritabanıyla birleştirilir, geçmiş sonuçlar korunur.
- Eski turnuva bazlı backfill devam eder.

Logda TEAM ... raw events=..., fallback leagues=..., fallback team matches=... alanlarını inceleyin.
Not: Bu yaklaşım ESPN'de bulunmayan organizasyonları sihirli biçimde eklemez; bazı takımlar için 10 maç hâlâ eksik kalabilir.
