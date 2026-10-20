# Maç Analiz Pro v4 — Ücretsiz zamanlanmış veri altyapısı

Bu sürüm, GitHub Actions ile Türkiye saatiyle 08:00 ve 20:00'de ESPN skor verilerini sorgulayıp `data.json` dosyasına kaydeder. Uygulama bu dosyayı okur. İlk kurulumda `data.json` boş olabilir; ilk başarılı görevden sonra dolacaktır.

## Kurulum (bilgisayara program yüklemeden)

1. GitHub.com'da ücretsiz bir hesapla **public** yeni repository oluşturun.
2. ZIP içindeki `mac-analiz-pro-v4` klasörünün **içeriğini**, `.github/workflows/update.yml` dahil, repository köküne yükleyin. `.github` gizli klasörünün yüklenmiş olduğundan emin olun.
3. GitHub repository > **Settings > Actions > General > Workflow permissions** bölümünde **Read and write permissions** seçin ve kaydedin (kuruluş ilkeleri izin vermiyorsa görev commit atamaz).
4. **Actions > Match data twice daily > Run workflow** ile ilk güncellemeyi elle çalıştırın. Görev başarısızsa Actions logunu inceleyin; ücretsiz kamuya açık veri uç noktalarının erişimi garanti değildir.
5. `data.json` dosyası dolduktan sonra repository > **Settings > Pages > Build and deployment** bölümünde `Deploy from a branch`, `main`, `/(root)` seçin. GitHub Pages adresiniz oluşacaktır.
6. Android Chrome ile Pages adresini açıp menüden **Ana ekrana ekle** seçin.

**Önemli:** GitHub Actions zamanlanmış görevleri tam belirtilen dakikada çalışmayı garanti etmez; yoğunlukta gecikebilir veya geçici olarak çalışmayabilir. Public repository'lerde uzun süre işlem olmaması zamanlanmış işleri devre dışı bırakabilir. Görev veri çekemezse mevcut `data.json` korunur.

**Kaynak ve kullanım:** ESPN skor uç noktaları resmi üçüncü taraf veri sözleşmesi değildir; erişim veya kullanım koşulları değişebilir. Bu sürüm iddaa.com bahis oranlarını almaz. Yüzdeler istatistiksel Poisson modelinin tahminleridir; kalibre edilmiş başarı garantisi değildir. Sakatlık/kadro verisi dahil değildir. Kullanıcıya bildirim gönderilmez.

**Yayın seçeneği:** GitHub Pages kullanılınca Netlify gerekmiyor. Netlify'a statik ZIP yüklemek tek başına GitHub Actions zamanlayıcısını etkinleştirmez.
