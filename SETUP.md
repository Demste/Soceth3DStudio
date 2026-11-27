# Windows kurulum rehberi

## İlk kurulum

Git ile bir kez klonlayın veya [Windows ZIP sürümünü](https://github.com/Demste/Soceth3DStudio/releases/latest) indirin. Kaynak derlemesi Node.js 22.12 veya üstünü gerektirir. Uygulama Python 3.11–3.13 kullanır; Python 3.12 önerilir. WebView2 ve Blender kurulu olmalıdır. Ollama yalnızca yerel AI düzenleme işlevine, ComfyUI ise görsel üretim işlevlerine gerekir.

```powershell
git clone https://github.com/Demste/Soceth3DStudio.git
cd Soceth3DStudio
.\setup.ps1 -NoLaunch
.\start.ps1
```

Kurulum GPU, VRAM, sürücü, RAM, disk ve yerel sağlayıcıları gösterir. **Install Recommended Models**, **Customize Models**, **Use Existing Local Models** veya **Skip Model Installation** seçin. Modeller ve gerekirse yaklaşık 2 GiB ComfyUI çalışma zamanı için yer ayırın. Katalog, indirme boyutlarının yanı sıra her modelin disk ihtiyacını gösterir. Açık tercihi olan komut satırı kurulumları:

```powershell
.\setup.ps1 -Models Skip -NoLaunch
.\setup.ps1 -Models Existing -NoLaunch
.\setup.ps1 -Models Recommended -NoLaunch
.\setup.ps1 -Models Custom -Model flux-klein,qwen-coder -NoLaunch
```

İlk komut uygulamayı kurar ve ağırlık indirmez. Recommended ve Custom seçenekleri seçilen dosyaları indirme yetkisi verir. Eksik Python veya Node için etkileşimli kurulum onay isteyebilir. Komut satırında paket yöneticisine açıkça izin vermek için **-InstallPrerequisites** seçeneğini kullanın. Varsayılan Python güncel değilse uygun yorumlayıcıya **SOCETH_BOOTSTRAP_PYTHON** ile yol verilebilir.

## Dosya konumları

Temiz kurulum varsayılan olarak **%LOCALAPPDATA%\\Soceth3DStudio** kullanır. Eski çalışma alanındaki tanınan **runtime** ve **user_data** dizinleri yerinde kalır. Varsayılan projeler, günlükler ve modeller Git kaynaklarına dahil edilmez.

```powershell
.\setup.ps1 -Models Skip -NoLaunch -DataPath "D:\StudioData" -ProjectsPath "D:\Projects" -ModelsPath "E:\AIModels" -CachePath "E:\StudioCache" -LogsPath "D:\StudioLogs"
```

**-RuntimePath** ve **-OllamaModelsPath** da desteklenir. Konum tercihi dosyaları otomatik taşımaz. Yeni boş bir konum seçmeden önce durdurun ve ilgili veri dizinlerini yedekleyin. **SOCETH_CONFIG** ayrı kurulum ayar dosyalarına, **SOCETH_CATALOG** ise bağımsız doğrulanmış katalog kopyasına işaret edebilir.

## Donanım ve modeller

- 8 GiB ölçülmüş kurulumda Pixal3D INT8, FLUX.2 Klein 4B FP8 ve Qwen2.5-Coder 7B Q4_K_M kullanılır.
- 16 GiB ve uygun CUDA ortamında daha yüksek çalışma ayarları önerilebilir. FLUX 4B BF16 ve Qwen Coder 14B, kaynakları uygun olduğunda karşılaştırma adayıdır. Pixal3D BF16 için 16 GiB uyumu doğrulanmamıştır.
- **Measured** çalışma sonucu, **Documented** yayıncı belgesi, **Estimated** planlama eşiği, **Experimental** denenmesi gereken varyant, **Incompatible** karşılanmayan gereksinim anlamına gelir. CUDA çekirdek doğrulaması model çıkarım testi yerine geçmez.

**Models** ekranında mevcut ComfyUI modeli veya Hugging Face snapshot klasörü seçin. Studio ağırlıkları hash ile doğrular ve dosyalara referans verir. Yerel Ollama etiketini kaydedebilir veya uyumlu GGUF dosyasını Ollama’ya içe aktarabilirsiniz. **Advanced mode → Local providers** bölümünde localhost portlarını veya mevcut ComfyUI kurulumunu girip Studio’yu yeniden başlatın. Studio tarafından yönetilen ComfyUI harici model yollarını yeniden başlatınca okur. Bağımsız çalıştırılan ComfyUI için aynı arama yollarını sağlayıcı ayarına ekleyin.

Model seçimini tüm projelere veya açık projeye kaydedin. Tek üretim için Advanced modundaki seçiciye geçin. Art Bible, kullandığınız yerel konsept ve rekonstrüksiyon iş akışına uygulanır. Karşılaştırmada aynı giriş, seed ve Art Bible sürümünü kullanın.

TRELLIS.2 INT8 ve BF16 ayrı native ComfyUI seçenekleridir. BF16 16 GiB kartta deneysel ve yalnızca 512 profilidir. TripoSR ve TripoSG için önce CUDA doğrulamasından geçen bir ComfyUI Python yorumlayıcısı kurun. Model Manager ilk kurulumda sabitlenmiş kaynak ve Python paketlerini ayrı bir çalışma klasörüne hazırlar. Bu iki model arka planı önceden kaldırılmış saydam PNG ister. TripoSR vertex rengini, TripoSG dokusuz geometriyi GLB'ye çıkarır. Art Bible doku ayarları bu modellerden yeni PBR dokuları üretmez.

## Blender, low-poly ve Unity

Kurulum **Soceth-Blender-Addon.zip** dosyasını runtime konumunda üretir. Blender’da ZIP’i yükleyin, eklentiyi etkinleştirin ve Studio’nun **Models → Advanced mode → Blender connection** alanında gösterdiği yerel token dosyasını seçin. Bağlandıktan sonra Workspace’teki komut önerisini inceleyip çalıştırın.

Art Bible bütçesine göre optimize edin, gerekli bölgelerde dokuyu eşleyin ve ana revizyonu onaylayın. **Create as LOD** ile ayrı mesafe revizyonu oluşturun. **Unity 6 URP** paketi ana modeli, LOD’ları, dokuları ve Editor içe aktarıcısını içerir. Unity projesine paket içindeki klasörleri ekleyin ve manifestten prefab oluşturun.

## Güncelleme ve geri dönüş

Git ile kurulan sürümde:

```powershell
.\update.ps1 -Check
.\stop.ps1
.\update.ps1
.\start.ps1
```

Güncelleme yerel değişiklik veya ayrışan dal varsa durur. Bitmemiş işleri kapatın. Uygulama kilitleri aynıysa Python ve Node bağımlılıkları yeniden yüklenmez. Kayıtlı modeller ve tercihler korunur. Blender için yeni ZIP üretildiğinde Blender’da yükleyip etkinleştirin.

ZIP kurulumunda yeni paketi ve yayınlanan **.sha256** dosyasını indirin. Mevcut kurulum klasöründe, indirme yolunu kendi dosyanıza göre ayarlayıp şu komutları çalıştırın. İlk iki satır v0.2.0 betiğinin Türkçe karakterli dizinlerde çalışması için UTF-8 ayarlar.

```powershell
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$zip = 'D:/Downloads/Soceth3DStudio-0.2.1-Windows.zip'
$sha = ((Get-Content -LiteralPath "$zip.sha256" -Raw).Trim() -split '\s+')[0]
.\stop.ps1
.\upgrade-release.ps1 -Archive $zip -Sha256 $sha
& '..\Soceth3DStudio-0.2.1\start.ps1'
```

Yeni sürüm ayrı dizine açılır. Eski kaynak dizini ve uygulama verileri korunur.

Git güncellemesinden geri dönüş için runtime içindeki **last-update.json** dosyasındaki **previous** sürümü ayrı bir dalda veya worktree’de açın. ZIP güncellemesinde eski dizini kullanın. Veritabanı yedeği **data/backups** altında tutulur. Sürüm değişimi veritabanı uyumsuzluğu yaratırsa Studio kapalıyken mevcut veritabanını koruyup ilgili yedeğin bir kopyasını kullanın.

## Sorun giderme

**.\\verify.ps1 -Detailed** ile ekran kartı ve gerçek CUDA sonucunu kontrol edin. GPU yükseltmesinde önce sürücüyü güncelleyin. Gerekirse **.\\setup.ps1 -WithComfy -Models Skip -NoLaunch** ile uygun sağlayıcıyı ayrı dizine kurun. GPU belleği doluysa daha düşük profil seçin. Eksik native node veya uyumsuz model açıklamasına Models kartından bakın. Gelişmiş mod yerel tanılama günlüklerini açar. Model koşullarını [lisans belgesinden](docs/LICENSES.md) inceleyin.

