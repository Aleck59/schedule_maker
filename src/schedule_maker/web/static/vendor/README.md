# Сторонние библиотеки

Файлы положены в репозиторий намеренно: в рантайме не нужны ни npm, ни сборка,
а версии не «уплывают» при переустановке. Все лицензии — MIT.

| Файл | Библиотека | Версия | Зачем |
|---|---|---|---|
| `tabler.min.css`, `tabler.min.js` | [Tabler](https://github.com/tabler/tabler) | 1.5.1 | Оформление интерфейса (внутри Bootstrap 5) |
| `htmx.min.js` | [htmx](https://htmx.org) | 2.0.4 | Обновление кусков страницы без написания JavaScript |
| `alpine.min.js` | [Alpine.js](https://alpinejs.dev) | 3.14.9 | Мелкая реактивность в разметке |
| `sortable.min.js` | [SortableJS](https://sortablejs.github.io/Sortable/) | 1.15.6 | Перетаскивание пар в конструкторе |

## Как обновить

```bash
cd src/schedule_maker/web/static/vendor
curl -sSfL -o tabler.min.css "https://cdn.jsdelivr.net/npm/@tabler/core@<версия>/dist/css/tabler.min.css"
curl -sSfL -o tabler.min.js  "https://cdn.jsdelivr.net/npm/@tabler/core@<версия>/dist/js/tabler.min.js"
```

После обновления поправьте версию в этой таблице.
