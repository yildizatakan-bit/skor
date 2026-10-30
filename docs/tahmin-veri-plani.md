# Tahmin modeli: veri kapsamı ve sürüm planı

## Bu sürümde

- Tamamlanmış maçların skoru ve tarihi kullanılır. Son skorun maç başında biliniyor görünmemesi için takım formu ve Elo hesabında 3 saatlik bitiş payı uygulanır.
- Takım gücü Elo puanı, rakip seviyesi ve mevcut maç geçmişiyle hesaplanır. Bu bilgi mevcut Poisson gol dağılımına sınırlı düzeltme olarak verilir.
- Yeni Elo tahminleri, aynı fikstür için üretilen Elo'suz Poisson olasılıklarıyla ayrı bir dosyada dondurulur. Eski tahmin arşivi ve maç veritabanı yeniden hesaplanmaz.
- Ölçümde maç sonucu isabeti, Brier skoru, log-loss ve en az %60 güvenli seçimlerin başarı oranı ile kapsama payı birlikte raporlanır.

## Geçmiş tekrarın gösterdiği

`match_archive.sqlite3` yalnızca okunarak yapılan kronolojik tekrar, yeterli geçmişi bulunan 1.547 maçı içerdi. İlk 80% dönem yalnızca model seçimi, son 20% dönem karşılaştırma için kullanıldı.

| Yöntem | Son dönem 1-X-2 isabeti | Brier (düşük daha iyi) | Log-loss (düşük daha iyi) |
|---|---:|---:|---:|
| Poisson | %52,6 | 0,6049 | 1,0110 |
| Poisson + Elo | %51,6 | 0,5914 | 0,9913 |

Elo olasılık puanlarını iyileştirdi fakat ham isabet yüzdesini yükseltmedi. Bu yüzden uygulama Elo için %70 başarı iddiasında bulunmaz. Eski arşivdeki 1-X-2 sonucu ayrı bir tarihsel ölçüm olarak kalır; yeni A/B kaydı yalnızca sürüm çıktıktan sonra maç öncesinde kaydedilen aynı maçları karşılaştırır.

Ayrıca saha içi/saha dışı son maçları ayrı ağırlıklandırma ve düşük skorlu beraberlik düzeltmesi kronolojik dönemde denendi. Son dönem Brier veya isabetinde iyileşme göstermedi; bu sürüme alınmadı.

## Şu an mevcut olmayan veya tahmine güvenle bağlanamayan veriler

| Veri | Depodaki durum | Entegrasyon koşulu |
|---|---|---|
| Maç önü 1-X-2 ve gol oranları | Tahmin arşivinde sistem olasılıkları var; doğrulanmış bookmaker oranı yok | Maç başlamadan zaman damgalı, komisyonsuzlaştırılmış oranı ayrı alanlarda sakla; model kıyaslamasında piyasa tabanını da raporla |
| Şut, isabetli şut, korner | Kalıcı maç arşivinde maç başına takım istatistikleri yok | Her iki takımın geçmiş maçlarında aynı kaynaktan yeterli kapsama ve skorla eşleşen olay kimliği olmalı; şut/korner için ayrı dağılım modelleri kur |
| xG | Geçmiş maç satırlarında yok | Kaynağın tanımı ve tarihsel kapsamı sabit olmalı; xG'yi gollerle karıştırmadan ayrı özellik olarak dondur |
| Kesin ilk 11, sakatlık ve ceza | Güncel kadro/oyuncu sezon verisi mevcut; geçmiş maç anına ait doğrulanmış ilk 11 ve yokluk kaydı yok | `observedAt` ve maç başlama saatinden önceki son durum saklanmalı; tahmin edilen kadro ile kesin kadro ayrı tutulmalı |
| Oyuncu maç başı şut/gol katkısı | BSD oyuncu listelerinde sezon toplamları var; ESPN ile güvenilir oyuncu kimliği eşlemesi ve geçmiş maç öncesi görüntü yok | Oyuncu kimliği, lig/sezon ve maç öncesi snapshot eşleşmesi doğrulanmadan ana modele katma; deneysel oyuncu motorunda ayrı ölç |
| Lig sıralaması ve maçın önemi | Arşivden kısmi tablo türetilebilir, ancak geçmiş tarihli resmî tablo görüntüsü eksik | Eksiksiz, tarih damgalı resmî tablolar gelene kadar puan/şampiyonluk baskısını ana modele ekleme |
| Dinlenme ve seyahat | Maç tarihlerinden dinlenme yaklaşık hesaplanabilir; tüm geçmiş fikstür ve saha konumu güvenilir değil | Takvim kapsamı ve stadyum konumu doğrulanırsa önce ayrı aday olarak dönem dışı kıyasla |

Her yeni girdi için maç kimliği, sağlayıcı, ölçüm zamanı, maç başlama zamanı, lig/sezon ve kapsama oranı kaydedilmelidir. Bir maçın sonucu görüldükten sonra oluşturulmuş veri o maçın tahminine geriye dönük eklenmemelidir.

## Sonraki veri toplama önceliği

1. Tarih damgalı maç önü oranları; de-vig edilmiş 1-X-2 oranı hem güçlü bir kıyas tabanı hem de aday özellik olur.
2. Takım başına maç başı şut, isabetli şut ve korner geçmişi; önce bu pazarların kendi tahmin modelleri ve kapsama raporu.
3. Tarih damgalı kesin ilk 11, kaleci, sakatlık ve cezalar; önce oyuncu etkisi ayrı modelde kıyaslanır.
4. xG ve resmî geçmiş lig tabloları ancak aynı tarihsel kalite ve kimlik eşleşmesi sağlanırsa eklenir.

Bir özellik ana tahmin motoruna, yalnızca geçmişte daha iyi görünmesiyle değil; zaman sıralı ayrılmış dönemde Brier/log-loss ve ilgili pazarın isabet ölçümünü iyileştirip bu sonuç yeni fikstürlerde de korunursa alınır.
