"""Publish the first five Gramly Welcome video lessons."""

import sqlalchemy as sa

from alembic import op


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


VIDEOS = (
    ("connect-bot-channel", "Подключение бота и канала", "Токен BotFather, права администратора и проверка подключения.", 19, "home", 10),
    ("first-welcome", "Первое приветствие", "Текст, форматирование, предпросмотр, канал и запуск.", 19, "flows", 20),
    ("media-buttons-links", "Медиа, кнопки и ссылки", "Креатив, индивидуальные цвета и управление превью ссылки.", 20, "flows", 30),
    ("use-template", "Использование шаблона", "Независимый черновик из каталога и настройка под свой канал.", 18, "flows", 40),
    ("audience-broadcast", "База и первая рассылка", "Согласие, активная аудитория и подтверждение отправки.", 21, "audience", 50),
)


def upgrade() -> None:
    bind = op.get_bind()
    statement = sa.text(
        """
        INSERT INTO learning_video
            (slug, title, description, video_url, poster_url,
             duration_seconds, feature_key, sort_order, is_active)
        SELECT :slug, :title, :description, :video_url, :poster_url,
               :duration, :feature, :sort_order, true
        WHERE NOT EXISTS (SELECT 1 FROM learning_video WHERE slug = :slug)
        """
    )
    for slug, title, description, duration, feature, sort_order in VIDEOS:
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


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM learning_video WHERE slug IN :slugs").bindparams(
            sa.bindparam("slugs", expanding=True)
        ),
        {"slugs": [video[0] for video in VIDEOS]},
    )
