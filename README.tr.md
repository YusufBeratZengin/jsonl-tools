# jsonl-tools
[![CI](https://github.com/YusufBeratZengin/jsonl-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/YusufBeratZengin/jsonl-tools/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/jsonl-tools)](https://pypi.org/project/jsonl-tools/) [![MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml) [![Bağımlılıklar](https://img.shields.io/badge/runtime_dependencies-0-green)](pyproject.toml)

[English](README.md) | [Türkçe](README.tr.md)

Büyük JSONL ve gzip dosyalarını CLI veya Python üzerinden akış halinde inceleyin ve dönüştürün.
Çalışma zamanı bağımlılığı yoktur; veriler bilgisayarınızda işlenir.
Kaynak koddan kurulum: `python -m pip install .`
Deneyin: `jsonl-tools filter examples/chat.jsonl "usage.tokens>1000" --count`

![Gerçek CLI çıktısı](docs/demo.svg)

## Demo

Kurulumdan sonra depo kökünde çalıştırın:

```console
jsonl-tools count examples/chat.jsonl
jsonl-tools filter examples/app.log.jsonl "level==ERROR" --count
jsonl-tools select examples/chat.jsonl id tokens=usage.tokens
jsonl-tools validate examples/chat.jsonl
```

İlk komut `12`, ikinci komut `5`, doğrulama ise
`12 line(s): 12 valid, 0 invalid` yazdırır. Tam çıktı
[docs/demo.txt](docs/demo.txt) dosyasındadır. SVG ve metni yeniden üretmek için
`python scripts/make-demo.py` çalıştırın; script gerçek CLI'ı sentetik örneklerle çağırır.

## Kurulum

Python 3.10 veya üzeri gerekir. Bu kaynak kod klasöründe:

```console
python -m pip install .
```

Sürüm 0.1.0'dır; paketin registry'de yayımlandığı varsayılmaz. Kurulum için
Hatchling build backend'i gerekir; kaynak koddan çalıştırmak için Python yeterlidir.
Kurulumdan sonra `python -m jsonl_tools` ile `jsonl-tools` aynı komutları sunar.

## Kullanım

### CLI

```console
jsonl-tools head examples/chat.jsonl -n 2
jsonl-tools tail examples/chat.jsonl -n 2
jsonl-tools stats examples/chat.jsonl --field usage.tokens
jsonl-tools schema examples/chat.jsonl
jsonl-tools sample examples/chat.jsonl -n 3 --seed 7
jsonl-tools dedupe examples/chat.jsonl --by model
jsonl-tools split examples/chat.jsonl --lines 5 --out-dir is/parts
```

Stdin için FILE yerine `-` verin. Filter ve select açıkça FILE ister; diğer
komutlar varsayılan olarak stdin okur. Shell'in `>`, `<` ve wildcard işaretlerini
yorumlamaması için ifadeleri tırnak içine alın. Her komutta `--help` vardır.

### Python API

```python
from jsonl_tools import SchemaInferrer, Stats, compile_expr, read_jsonl

matches = compile_expr("usage.tokens>1000")
stats, schema = Stats(), SchemaInferrer()
for record in read_jsonl("examples/chat.jsonl"):
    if matches(record):
        stats.add(record)
        schema.add(record)
print(stats.result()["records"])  # 2
print(schema.schema()["type"])  # object
```

`read_jsonl(path, skip_bad=False)` kayıtları ihtiyaç oldukça okur; string ve `Path`
kabul eder. Geçersiz kayıtta fiziksel satır numarasını içeren `BadLineError` yükseltir.
`open_input`, `open_output`, `iter_lines`, `iter_records`, alan yolu yardımcıları ve
`jsonl_tools.ops` işlemleri de kullanılabilir. `Stats.result()` ve
`SchemaInferrer.schema()` JSON'a dönüştürülebilen sözlükler döndürür.

## Örnekler

[examples/chat.jsonl](examples/chat.jsonl) sentetik model konuşmaları,
[examples/app.log.jsonl](examples/app.log.jsonl) sentetik servis logları içerir.
Örnekler model veya dış servis çağırmaz.

```console
jsonl-tools filter examples/chat.jsonl "model==demo-large" --and "usage.tokens>1000" --count
jsonl-tools select examples/chat.jsonl id "roles=messages[].role" --null-missing
jsonl-tools validate examples/chat.jsonl --object --json
```

Alan yollarında `a.b`, `a[0]`, negatif indeksler ve `a[].b` desteklenir. `[]`,
filter'da herhangi bir elemanın eşleşmesini arar; select'te eşleşmeleri listeler.
Eksik alan `null` değerinden farklıdır. Select iç içe nesne anahtarlarını korur;
alias, indeks, wildcard ve `--flat` düz anahtar üretir. Adında nokta veya köşeli
parantez bulunan anahtarlar için escape sözdizimi yoktur.

## Yapılandırma / seçenekler

Yapılandırma dosyası veya ortam değişkeni gerekmez.

| Komut | Temel seçenekler | Davranış |
| --- | --- | --- |
| `head`, `tail` | `-n N`, `-o FILE` | İlk/son N dolu satır; varsayılan 10 |
| `count` | FILE | Dolu satır sayısı; JSON doğrulamaz |
| `filter` | EXPR, `--and`, `--or`, `--invert`, `--count` | `(EXPR veya alternatifler) ve bütün ek koşullar` |
| `select` | FIELD…, `alias=path`, `--flat`, `--null-missing` | Alan seçme ve yeniden adlandırma |
| `stats` | `--json`, `--field`, `--top`, `--max-paths`, `--max-depth` | Alan varlığı, türler, sayısal özet ve sık değerler |
| `schema` | `--no-required`, `--max-props` | Draft 2020-12 JSON Schema çıkarımı |
| `split` | `--lines N` veya `--size SIZE`, `--out-dir`, `--prefix`, `--gzip` | Kaydı bölmeden numaralı dosyalar üretir |
| `sample` | `-n N` veya `--fraction P`, `--seed` | Girdi sırasını koruyan tekrarlanabilir örneklem |
| `dedupe` | `--by PATH`, `--canonical`, `--verbose`, `-o FILE` | İlk kaydı tutup tekrarları eler |
| `validate` | `--object`, `--json`, `--quiet`, `--limit`, `--clean FILE` | Hataları bildirir; sağlam kayıtları ayrı kaydedebilir |

Filter operatörleri: `== != > >= < <= ~ !~`, `PATH exists`, `PATH missing`.
Sağ taraf JSON değeri veya tırnaksız string olabilir; regex için Python `re`
kullanılır. Eşitlik boolean ile sayıyı ayırır. `!=` ve `!~` herhangi bir eşleşmenin
tersidir; eksik alanlar bu koşulları sağlar. Python kodu değerlendirilmez.

`--skip-bad`; filter, select, stats, schema ve alan bazlı dedupe için geçerlidir.
Atlanan toplam satır sayısı ve ilk beş satır numarası stderr'e yazılır.
Ham/canonical dedupe geçersiz satırları tutar; ayrıştırılamayanları byte olarak
karşılaştırır. Gzip girdi içerikten tanınır; `-o FILE.gz` ve `--clean FILE.gz`
sıkıştırır. Split boyutları 1024 tabanlıdır (`64k`, `2MiB`); LF dahil sıkıştırılmamış
çıktı boyutunu ölçer. Büyük bir satır tek başına parça olur. Mevcut parça
dosyalarının üzerine yazılır; yeni bir çıktı klasörü kullanın. Aynı dosyayı
girdi ve çıktı olarak kullanmak reddedilir.

Çıkış kodları: **0** başarılı, **1** doğrulamada geçersiz satır bulundu,
**2** girdi/argüman/işleme hatası; kesilen komutlar **130** döndürür.

## Nasıl çalışır?

Girdi UTF-8'dir; başta BOM olabilir. Boş satırlar atlanır, çıktı LF kullanır.
Ham işlemler satır çevresindeki boşluğu temizler, JSON biçimini korur; select
kompakt JSON üretir. Ayrıştırılan işlemlerde her dolu satır tek JSON değeri
içermelidir. `NaN` ve `Infinity` reddedilir. Normal dosyada tail sondan okur;
gzip ve stdin için son N satırı bellekte tutar.

Stats varsayılan olarak 500 alan yolunu 12 derinliğe kadar izler; kök derinliği
0'dır. Her alanda 4.096 sayılık reservoir ve 1.000 sık değer adayı tutulur.
Yaklaşık yüzdelik ve sıklık sonuçları işaretlenir. Schema nesne başına 200 anahtar
ve 32 derinlikle sınırlıdır; zorunlu alanlar gözlenen veriden çıkarılır.

## Sınırlamalar / gizlilik

İşlemler yereldir; telemetri veya ağ isteği yoktur. Çıktı girdi verisini
içerebilir; dosya konumunu buna göre seçin. Bellek; en büyük satıra, tail/sample
boyutuna ve izlenen yollara bağlıdır. Dedupe her benzersiz anahtar için 128-bit
özet ve Python set ek yükü tutar; teorik hash çakışması riski vardır. Split çıktı
dosyası adlarını tutar. Wildcard büyük dizileri genişletebilir. Regex zaman
aşımı yoktur; güvendiğiniz ifadeleri kullanın.

Schema çıkarımı, hazır bir şemaya göre doğrulama yapmaz. Sayısal özetler kayan
noktalıdır; sonlu float aralığını aşan değerler tür sayımına katılır, sayısal
özete katılmaz. Her JSON anahtarı yol sözdizimiyle adreslenemez. Hatalı bir işlem
kısmi çıktı bırakabilir; yazma işlemleri atomik değildir.

## Geliştirme

Kaynak kod klasöründe, geliştirme bağımlılıklarıyla:

```console
python -m pip install -e ".[dev]"
python -m ruff check .
python -m mypy src
python -m pytest --cov=jsonl_tools --cov-report=term-missing --cov-fail-under=85
python scripts/make-demo.py --check
python -m build
python -m twine check dist/*
```

CI, Python 3.10–3.13 sürümlerini Linux ve Windows'ta test eder; branch dahil
kapsamın en az %85 olmasını zorunlu tutar. Testler sentetik veri kullanır, ağa
çıkmaz. Çevrimdışı çalışma ve sürüm hazırlığı için
[CONTRIBUTING.md](CONTRIBUTING.md) dosyasına bakın.

## Katkı

[CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) ve
[SECURITY.md](SECURITY.md) belgelerini okuyun. Değişiklikler
[CHANGELOG.md](CHANGELOG.md) içinde tutulur.

## Lisans

[MIT](LICENSE) © 2026 Yusuf Berat Zengin.
