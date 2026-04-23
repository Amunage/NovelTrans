from __future__ import annotations

from urllib.parse import urlparse

from app.extract.site.base import Chapter, SiteExtractor
from app.extract.site.kakuyomu import KakuyomuExtractor
from app.extract.site.narou import NarouExtractor
from app.extract.site.pixiv import PixivExtractor
from app.extract.site.syosetu_org import SyosetuOrgExtractor
from app.extract.site.twkan import TwkanExtractor


EXTRACTORS: tuple[type[SiteExtractor], ...] = (
    SyosetuOrgExtractor,
    NarouExtractor,
    KakuyomuExtractor,
    PixivExtractor,
    TwkanExtractor,
)


def resolve_extractor(url: str) -> SiteExtractor:
    host = urlparse(url).netloc.lower()
    if not host:
        raise ValueError("유효한 URL을 입력해주세요.")

    for extractor_class in EXTRACTORS:
        if extractor_class.supports(host):
            return extractor_class()

    supported_sites = ", ".join(extractor.site_name for extractor in EXTRACTORS)
    raise ValueError(f"지원하지 않는 사이트입니다. 현재 지원: {supported_sites}")


__all__ = ["Chapter", "SiteExtractor", "resolve_extractor"]