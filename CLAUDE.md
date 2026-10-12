# Claude için proje kuralları

## Commit ve PR atfı

- Commit mesajlarına `Co-Authored-By: Claude …` veya `Claude-Session: …` satırı **eklenmez**. Claude kendini ortak
  yazar olarak göstermez.
- Commit yazarı, oturumu kullanan geliştiricinin kendi git kimliğidir (GitHub hesabı). `Claude <noreply@anthropic.com>`
  yazar olarak kullanılmaz; kimlik ayarlı değilse commit'ten önce geliştiriciye sorulur.
- PR açıklamalarına "Generated with Claude Code" veya oturum bağlantısı eklenmez.

## Belgeler

- Mimari ve öğrenme rehberi: `docs/ogrenme-rehberi/README.md`
- Ortak kod kuralı: ortak dosyalar `mevzuat-core`'da değiştirilir, `python tools/shared.py sync` ile crawler'a eşitlenir.
