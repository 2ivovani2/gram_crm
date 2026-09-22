"""Replace the original lessons with the complete Gramly video library.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


OLD_VIDEOS = (
    ("connect-bot-channel", "Подключение бота и канала", "Токен BotFather, права администратора и проверка подключения.", 19, "home", 10),
    ("first-welcome", "Первое приветствие", "Текст, форматирование, предпросмотр, канал и запуск.", 19, "flows", 20),
    ("media-buttons-links", "Медиа, кнопки и ссылки", "Креатив, индивидуальные цвета и управление превью ссылки.", 20, "flows", 30),
    ("use-template", "Использование шаблона", "Независимый черновик из каталога и настройка под свой канал.", 18, "flows", 40),
    ("audience-broadcast", "База и первая рассылка", "Согласие, активная аудитория и подтверждение отправки.", 21, "audience", 50),
)

VIDEOS = (
    ("01-podklyuchenie-bota-i-kanala", "Подключение бота и канала", "Создание бота, добавление канала и проверка прав.", 24, "home", 10),
    ("02-gotovyy-shablon", "Готовый шаблон", "Выбор готового шаблона и создание собственного черновика.", 26, "flows", 20),
    ("03-privetstvie-s-nulya", "Приветствие с нуля", "Создание нового приветствия без шаблона.", 23, "flows", 30),
    ("04-oformlenie-soobshcheniya", "Оформление сообщения", "Текст, медиа, форматирование и кнопки сообщения.", 23, "flows", 40),
    ("05-tsepochka-i-zaderzhki", "Цепочка и задержки", "Несколько сообщений и интервалы между отправками.", 27, "flows", 50),
    ("06-proverka-i-zapusk", "Проверка и запуск", "Предпросмотр, выбор каналов и запуск приветствия.", 25, "flows", 60),
    ("07-proshchalnoe-soobshchenie", "Прощальное сообщение", "Настройка сообщения для участника, который покидает канал.", 28, "flows", 70),
    ("08-zayavki-na-vstuplenie", "Заявки на вступление", "Обработка заявок и автоматическое принятие участников.", 23, "bots", 80),
    ("09-baza-podpischikov", "База подписчиков", "Сбор согласий и работа с активной аудиторией.", 25, "audience", 90),
    ("10-rassylka", "Рассылка", "Создание сообщения и запуск рассылки по базе.", 24, "broadcasts", 100),
    ("11-rotatsiya-kanalov", "Ротация каналов", "Настройка порядка и приоритетов подключённых каналов.", 23, "rotation", 110),
    ("12-analitika", "Аналитика", "Просмотр доставок, аудитории и результатов сценариев.", 23, "analytics", 120),
    ("13-tarif-business", "Тариф «Бизнес»", "Возможности тарифа и управление подпиской.", 24, "subscription", 130),
    ("14-partnerskaya-programma", "Партнёрская программа", "Реферальная ссылка, начисления и выплаты партнёру.", 24, "referral", 140),
)


def _insert(videos: tuple[tuple[str, str, str, int, str, int], ...]) -> None:
    statement = sa.text(
        """
        INSERT INTO learning_video
            (slug, title, description, video_url, poster_url,
             duration_seconds, feature_key, sort_order, is_active)
        VALUES
            (:slug, :title, :description, :video_url, :poster_url,
             :duration, :feature, :sort_order, true)
        ON CONFLICT (slug) DO UPDATE SET
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            video_url = EXCLUDED.video_url,
            poster_url = EXCLUDED.poster_url,
            duration_seconds = EXCLUDED.duration_seconds,
            feature_key = EXCLUDED.feature_key,
            sort_order = EXCLUDED.sort_order,
            is_active = true,
            updated_at = now()
        """
    )
    bind = op.get_bind()
    for slug, title, description, duration, feature, sort_order in videos:
        bind.execute(
            statement,
            {
                "slug": slug,
                "title": title,
                "description": description,
                "video_url": f"https://gramly.tech/static/learning/{slug}.mp4",
                "poster_url": f"https://gramly.tech/static/learning/{slug}.jpg",
                "duration": duration,
                "feature": feature,
                "sort_order": sort_order,
            },
        )


def _delete(videos: tuple[tuple[str, str, str, int, str, int], ...]) -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM learning_video WHERE slug IN :slugs").bindparams(
            sa.bindparam("slugs", expanding=True)
        ),
        {"slugs": [video[0] for video in videos]},
    )


def upgrade() -> None:
    _delete(OLD_VIDEOS)
    _insert(VIDEOS)


def downgrade() -> None:
    _delete(VIDEOS)
    _insert(OLD_VIDEOS)
