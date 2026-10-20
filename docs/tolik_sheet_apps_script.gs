/**
 * Толік → Google-таблиця «Толік — комфорт».
 * 1) Створіть Google-таблицю «Толік — комфорт» (акаунт vadym.ilike@gmail.com).
 * 2) Розширення → Apps Script → вставте цей код → Зберегти.
 * 3) Project Settings → Script Properties → додайте KEY = будь-який довгий випадковий рядок.
 * 4) Deploy → New deployment → Web app → Execute as: Me, Who has access: Anyone → Deploy.
 * 5) Адресу веб-застосунку + ?key=ВАШ_KEY вставте в Railway → Variables → TOLIK_SHEET_WEBHOOK.
 */
const HEADERS = ['Записано', 'Дата', 'Роль', 'Хто', 'Зал', 'Поверх', 'Приміщення', 'Час заняття', 'Стиль',
                 'Відповідь', 'Термометр', 'Тест'];
const HALLS = {white: 'Сірий', blue: 'Синій', purple: 'Фіолетовий', green: 'Зелений', pink: 'Рожевий'};
const ANSWERS = {cold: 'Холодно', normal: 'Нормально', hot: 'Жарко'};
const ROOMS = {hall: 'зал', reception: 'рецепція', dressing: 'роздягалка', off: 'не працювала'};

function doPost(e) {
  const key = PropertiesService.getScriptProperties().getProperty('KEY');
  if (!key || e.parameter.key !== key) return ContentService.createTextOutput('forbidden');
  const d = JSON.parse(e.postData.contents);
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  if (sh.getLastRow() === 0) sh.appendRow(HEADERS);
  sh.appendRow([
    new Date(), d.survey_date || '', d.role === 'admin' ? 'фея-адміністратор' : 'хореограф', d.person || '',
    HALLS[d.hall] || '', d.floor || '', ROOMS[d.room] || d.room || '', d.class_time || '', d.style || '',
    ANSWERS[d.answer] || '', d.thermometer == null ? '' : d.thermometer, d.is_test ? 'так' : ''
  ]);
  return ContentService.createTextOutput('ok');
}
