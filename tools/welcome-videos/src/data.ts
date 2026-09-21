export type Tutorial = {
  id: string;
  number: string;
  title: string;
  subtitle: string;
  feature: string;
  duration: number;
  scenes: Array<{
    eyebrow: string;
    title: string;
    note: string;
    screen: "connect" | "welcome" | "creative" | "template" | "audience";
  }>;
};

export const tutorials: Tutorial[] = [
  {
    id: "connect-bot-channel",
    number: "01",
    title: "Подключение бота и канала",
    subtitle: "Два подключения — и Gramly готов встречать участников.",
    feature: "home",
    duration: 19,
    scenes: [
      { eyebrow: "ШАГ 1", title: "Откройте Gramly", note: "Нажмите системную кнопку в профиле owner-бота.", screen: "connect" },
      { eyebrow: "ШАГ 2", title: "Добавьте своего бота", note: "Вставьте токен BotFather. Сообщение с токеном сразу удалится.", screen: "connect" },
      { eyebrow: "ШАГ 3", title: "Подключите канал", note: "Добавьте бота администратором и нажмите «Проверить подключение».", screen: "connect" },
    ],
  },
  {
    id: "first-welcome",
    number: "02",
    title: "Первое приветствие",
    subtitle: "Соберите сообщение, проверьте его и запустите на канале.",
    feature: "flows",
    duration: 19,
    scenes: [
      { eyebrow: "ШАГ 1", title: "Откройте «Приветствие»", note: "Создайте пустой черновик или возьмите готовый шаблон.", screen: "welcome" },
      { eyebrow: "ШАГ 2", title: "Напишите текст", note: "Добавьте {name}, выделение, цитату и Premium-эмодзи.", screen: "welcome" },
      { eyebrow: "ШАГ 3", title: "Проверьте и запустите", note: "Выберите канал — публикация и назначение сохранятся вместе.", screen: "welcome" },
    ],
  },
  {
    id: "media-buttons-links",
    number: "03",
    title: "Медиа, кнопки и ссылки",
    subtitle: "Оформите Telegram-пост до подтверждения сообщения.",
    feature: "flows",
    duration: 20,
    scenes: [
      { eyebrow: "МЕДИА", title: "Добавьте креатив", note: "Фото или видео живёт внутри карточки сообщения.", screen: "creative" },
      { eyebrow: "КНОПКИ", title: "Настройте каждую кнопку", note: "Название, ссылка, ряд и цвет задаются отдельно.", screen: "creative" },
      { eyebrow: "ССЫЛКИ", title: "Управляйте превью", note: "Отключите карточку ссылки, если она мешает композиции.", screen: "creative" },
    ],
  },
  {
    id: "use-template",
    number: "04",
    title: "Использование шаблона",
    subtitle: "Готовая структура превращается в ваш независимый черновик.",
    feature: "flows",
    duration: 18,
    scenes: [
      { eyebrow: "КАТАЛОГ", title: "Выберите сценарий", note: "Смотрите состав, длительность и Premium-эмодзи до установки.", screen: "template" },
      { eyebrow: "КОПИЯ", title: "Получите свой черновик", note: "Шаблон и чужие цепочки не изменятся.", screen: "template" },
      { eyebrow: "ЗАПУСК", title: "Адаптируйте под канал", note: "Поменяйте текст, медиа и кнопки, затем выберите канал.", screen: "template" },
    ],
  },
  {
    id: "audience-broadcast",
    number: "05",
    title: "База и первая рассылка",
    subtitle: "Собирайте согласия и отправляйте пост только активной аудитории.",
    feature: "audience",
    duration: 21,
    scenes: [
      { eyebrow: "СОГЛАСИЕ", title: "Добавьте кнопку «В базу»", note: "Подписчик подтверждает условия и проходит emoji-captcha.", screen: "audience" },
      { eyebrow: "АУДИТОРИЯ", title: "Проверьте активную базу", note: "Источник, канал, дата согласия и статус видны в одном списке.", screen: "audience" },
      { eyebrow: "РАССЫЛКА", title: "Создайте и запустите пост", note: "Перед отправкой Gramly покажет точное число получателей.", screen: "audience" },
    ],
  },
];
