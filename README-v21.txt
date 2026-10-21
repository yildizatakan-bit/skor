MAÇ ANALİZ PRO v21 – TAKIM BAZLI SON 10 MAÇ

ZIP içindeki dosyaları GitHub deposundaki aynı konumlara yükleyin.
Özellikle scripts/update_data.py ve .github/workflows/update.yml güncellenmeli.
match_archive.sqlite3 dosyasını silmeyin veya ZIP ile değiştirmeyin.

İlk aşama: 7 günlük fikstürde 10 tamamlanmış maçı eksik olan takımlar saptanır.
Her çalışmada 12 takımın ESPN takım fikstürü sezon bazlı sorgulanır.
Bir takımın farklı yarışmalarda kaydı varsa bunlar birleştirilir.
ESPN takım takvimi başarısız olursa eski turnuva taraması çalışmaya devam eder.
Log: TEAM BACKFILL ve TEAM <ad>: <eski> -> <yeni>/10.

Ek düzeltme: Turnuva arşivi taraması ilk altı lige takılı kalmaz, diğerlerine sıra gelir.

DİKKAT: ESPN resmi/sözleşmeli bir API değildir. Takım takvim endpointlerinin
kapsamı ve tüm ülke/turnuvalarda çalışması gerçek GitHub koşusunda doğrulanmalıdır.
10 maç her takım için garanti edilemez.
