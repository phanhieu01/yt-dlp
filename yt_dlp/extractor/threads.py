import json
import re

from .instagram import InstagramBaseIE
from ..utils import (
    ExtractorError,
    int_or_none,
    str_or_none,
    traverse_obj,
    unescapeHTML,
    url_or_none,
)


class ThreadsIE(InstagramBaseIE):
    IE_NAME = 'threads'
    IE_DESC = 'Instagram Threads'
    _VALID_URL = r'https?://(?:www\.)?threads\.(?:com|net)/@(?P<username>[^/]+)/(?:post/)?(?P<id>[^/?#&]+)'

    _TESTS = [{
        'url': 'https://www.threads.com/@hal2400ai/post/DQdLh4JiHfK',
        'info_dict': {
            'id': 'DQdLh4JiHfK',
            'ext': 'mp4',
            'title': 'Post by hal2400ai',
        },
        'skip': 'Post may not be available',
    }, {
        'url': 'https://www.threads.com/@uyeenne._/post/DUiYNmcj1PY',
        'info_dict': {
            'id': 'DUiYNmcj1PY',
            'ext': 'jpg',
            'title': 'Đeo cái tất vô mà t tưởng yêu quái =)))',
        },
        'skip': 'Image post',
    }]

    # Threads GraphQL API endpoints
    _THREADS_API_BASE = 'https://www.threads.net/api/graphql'
    _THREADS_APP_ID = '238260118697367'
    _POST_DOC_ID = '5587632691339264'

    @property
    def _threads_api_headers(self):
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'X-IG-App-ID': self._THREADS_APP_ID,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://www.threads.net',
            'Referer': 'https://www.threads.net/',
            'Accept': '*/*',
        }

    def _extract_all_media_from_html(self, webpage):
        """Extract all media URLs from HTML page"""
        urls = set()

        # 1. Open Graph images (multiple variants may exist)
        og_images = re.findall(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', webpage)
        urls.update(og_images)

        # 2. Open Graph image:url variants
        og_image_urls = re.findall(r'<meta[^>]*property=["\']og:image:url["\'][^>]*content=["\']([^"\']+)["\']', webpage)
        urls.update(og_image_urls)

        # 3. Open Graph image:secure_url variants
        og_secure_urls = re.findall(r'<meta[^>]*property=["\']og:image:secure_url["\'][^>]*content=["\']([^"\']+)["\']', webpage)
        urls.update(og_secure_urls)

        # 4. Twitter images
        twitter_images = re.findall(r'<meta[^>]*name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)["\']', webpage)
        urls.update(twitter_images)

        # 5. Twitter image:src variants
        twitter_src = re.findall(r'<meta[^>]*name=["\']twitter:image:src["\'][^>]*content=["\']([^"\']+)["\']', webpage)
        urls.update(twitter_src)

        # 6. Preload images/videos (from <link rel="preload">)
        # Match: <link rel="preload" as="image" href="...">
        preload_urls = re.findall(
            r'<link[^>]*rel=["\']preload["\'][^>]*href=["\']([^"\']*\.(?:jpg|jpeg|png|webp|mp4)[^"\']*)["\']',
            webpage,
            re.IGNORECASE
        )
        urls.update(preload_urls)

        # 7. All Instagram CDN URLs (scontent, instagram.fsgn, instagram.fna, etc.)
        # Match URLs from Instagram CDN that point to media files
        cdn_urls = re.findall(
            r'(https://(?:scontent|instagram\.[^./"\']*\.fna|instagram\.[^./"\']*\.fbcdn|[^./"\']*\.cdninstagram)[^"\'<>\s]*?\.(?:jpg|jpeg|png|webp|mp4)[^"\'<>\s]*)',
            webpage,
            re.IGNORECASE
        )
        urls.update(cdn_urls)

        # 8. Look for URLs in preload section (common pattern)
        preload_section = re.findall(
            r'"preload":\s*\[{([^\]]+(?:,\s*[^\]]+)*)\}',
            webpage
        )
        for match in preload_section:
            # Extract URLs from JSON array
            url_matches = re.findall(r'"([^"]*\.(?:jpg|jpeg|png|webp|mp4)[^"]*)"', match)
            urls.update(url_matches)

        # Filter unique and valid URLs
        media_urls = []
        seen = set()
        for url in urls:
            # Unescape HTML entities (&amp; -> &)
            url = unescapeHTML(url)

            # Unescape URLs (handle \/ -> /)
            url = url.replace('\\/', '/')

            # Validate URL
            if url and url.startswith('http') and url_or_none(url):
                # Remove query parameters for deduplication (keep the original URL)
                url_for_dedup = re.sub(r'[?&].*', '', url)
                if url_for_dedup not in seen:
                    seen.add(url_for_dedup)
                    media_urls.append(url)

        return media_urls

    def _create_playlist_result(self, post_id, title, description, media_urls, url):
        """Create a playlist result from multiple media URLs"""
        entries = []

        for idx, media_url in enumerate(media_urls):
            # Detect extension from URL
            if '.mp4' in media_url.lower():
                ext = 'mp4'
                format_type = 'video'
            elif '.webp' in media_url.lower():
                ext = 'webp'
                format_type = 'photo'
            elif '.png' in media_url.lower():
                ext = 'png'
                format_type = 'photo'
            else:
                ext = 'jpg'
                format_type = 'photo'

            entries.append({
                'id': f'{post_id}_{idx + 1}',
                'title': f'{title} - Media {idx + 1}' if title else f'Media {idx + 1}',
                'formats': [{
                    'url': media_url,
                    'format_id': f'{format_type}_{idx + 1}',
                    'ext': ext,
                    'http_headers': {
                        'Referer': 'https://www.threads.net/',
                    },
                }],
            })

        return {
            '_type': 'playlist',
            'id': post_id,
            'title': title,
            'description': description,
            'entries': entries,
        }

    def _fetch_post_data(self, url, post_id):
        """Fetch post data from Threads page (fallback to HTML parsing)"""
        # Try GraphQL API first
        try:
            variables = {'postID': post_id}
            post_data = f'variables={json.dumps(variables, separators=(",", ":"))}&doc_id={self._POST_DOC_ID}'

            response = self._download_json(
                self._THREADS_API_BASE,
                post_id,
                headers=self._threads_api_headers,
                data=post_data.encode(),
                note='Downloading Threads post data'
            )

            post_data = traverse_obj(response, ('data', 'data', 'containing_thread', 'thread'))
            if post_data:
                return post_data
        except Exception as e:
            self.report_warning(f'GraphQL API failed: {e}, falling back to HTML parsing')

        # Fallback: Parse HTML page
        self.report_warning('Using HTML parsing fallback (metadata may be limited)')
        webpage = self._download_webpage(url, post_id)

        # Try to extract data from embedded JSON
        # Threads might embed data in __NEXT_DATA__ or similar
        for pattern, name in [
            (r'__NEXT_DATA__\s*=\s*({.+?})\s*<', 'NEXT_DATA'),
            (r'window\._sharedData\s*=\s*({.+?})\s*;', 'sharedData'),
            (r'<script\s+type="application/json"\s+id="__NEXT_DATA__">(.+?)</script>', 'NEXT_DATA_script'),
        ]:
            json_data = self._search_json(pattern, webpage, name, post_id, fatal=False)
            if json_data:
                # Try to extract post data from JSON
                extracted = traverse_obj(
                    json_data,
                    ('props', 'pageProps', 'post', {dict}),
                    ('props', 'pageProps', 'thread', {dict}),
                    expected_type=dict
                )
                if extracted:
                    return extracted

        # Final fallback: Extract from Open Graph metadata
        self.report_warning('Using Open Graph metadata fallback')
        thumbnail = self._og_search_thumbnail(webpage)
        description = self._og_search_description(webpage)
        title = self._og_search_title(webpage) or description or f'Post by {username}'

        # Extract ALL media URLs for carousel support
        all_media_urls = self._extract_all_media_from_html(webpage)

        if not all_media_urls:
            raise ExtractorError('Could not extract any media from Threads page')

        # If multiple URLs found, return as playlist
        if len(all_media_urls) > 1:
            self.report_warning(f'Found {len(all_media_urls)} media items in carousel')
            return self._create_playlist_result(post_id, title, description, all_media_urls, url)

        # Single media - return standard info dict
        return {
            'id': post_id,
            'title': title,
            'description': description,
            'thumbnail': thumbnail,
            'formats': [{
                'url': all_media_urls[0],
                'format_id': 'og_image',
                'ext': 'jpg',
                'http_headers': {
                    'Referer': 'https://www.threads.net/',
                },
            }],
            'webpage_url': url,
            'extractor_key': ThreadsIE.ie_key(),
            'extractor': 'Threads',
        }

    def _extract_media_formats(self, post_data, post_id):
        """Extract video/image URLs from post data"""
        formats = []
        thumbnails = []

        # Check for video
        video_versions = traverse_obj(post_data, ('video_versions', ...))
        if video_versions:
            for video in video_versions:
                video_url = url_or_none(video.get('url'))
                if video_url:
                    formats.append({
                        'url': video_url,
                        'format_id': str_or_none(video.get('id')),
                        'width': int_or_none(video.get('width')),
                        'height': int_or_none(video.get('height')),
                        'filesize': int_or_none(video.get('size')),
                        'vcodec': str_or_none(video.get('video_codec')),
                        'http_headers': {
                            'Referer': 'https://www.threads.net/',
                        },
                    })

        # Check for image (if no video)
        if not formats:
            image_versions = traverse_obj(post_data, ('image_versions2', 'candidates', ...))
            if image_versions:
                for img in image_versions:
                    img_url = url_or_none(img.get('url'))
                    if img_url:
                        thumbnails.append({
                            'url': img_url,
                            'width': int_or_none(img.get('width')),
                            'height': int_or_none(img.get('height')),
                        })
                        # Use the largest image as the main format
                        if not formats:
                            formats.append({
                                'url': img_url,
                                'format_id': 'photo',
                                'ext': 'jpg',
                                'width': int_or_none(img.get('width')),
                                'height': int_or_none(img.get('height')),
                                'http_headers': {
                                    'Referer': 'https://www.threads.net/',
                                },
                            })

        # Check for carousel media
        carousel_media = traverse_obj(post_data, ('carousel_media', ...))
        if carousel_media and len(carousel_media) > 1:
            # For carousel, we'll create a playlist
            entries = []
            for idx, item in enumerate(carousel_media):
                entry = self._extract_carousel_item(item, post_id, idx)
                if entry:
                    entries.append(entry)

            if entries:
                return {
                    '_type': 'playlist',
                    'entries': entries,
                    'title': traverse_obj(post_data, ('caption', 'text')) or 'Threads post',
                }

        return {
            'formats': formats,
            'thumbnails': thumbnails,
        }

    def _extract_carousel_item(self, item, post_id, index):
        """Extract a single item from carousel media"""
        formats = []
        thumbnails = []

        # Check for video in carousel item
        video_versions = traverse_obj(item, ('video_versions', ...))
        if video_versions:
            for video in video_versions:
                video_url = url_or_none(video.get('url'))
                if video_url:
                    formats.append({
                        'url': video_url,
                        'format_id': str_or_none(video.get('id')),
                        'width': int_or_none(video.get('width')),
                        'height': int_or_none(video.get('height')),
                        'http_headers': {
                            'Referer': 'https://www.threads.net/',
                        },
                    })

        # Check for image in carousel item
        if not formats:
            image_versions = traverse_obj(item, ('image_versions2', 'candidates', ...))
            if image_versions:
                for img in image_versions:
                    img_url = url_or_none(img.get('url'))
                    if img_url:
                        thumbnails.append({
                            'url': img_url,
                            'width': int_or_none(img.get('width')),
                            'height': int_or_none(img.get('height')),
                        })
                        if not formats:
                            formats.append({
                                'url': img_url,
                                'format_id': 'photo',
                                'ext': 'jpg',
                                'width': int_or_none(img.get('width')),
                                'height': int_or_none(img.get('height')),
                                'http_headers': {
                                    'Referer': 'https://www.threads.net/',
                                },
                            })

        if formats:
            return {
                'id': f'{post_id}_{index + 1}',
                'title': f'Carousel item {index + 1}',
                'formats': formats,
                'thumbnails': thumbnails,
            }

        return None

    def _extract_metadata(self, post_data, username):
        """Extract metadata from post data"""
        caption_text = traverse_obj(post_data, ('caption', 'text')) or ''

        return {
            'title': caption_text or f'Post by {username}',
            'description': caption_text,
            'uploader': username,
            'uploader_id': str_or_none(traverse_obj(post_data, ('user', 'pk'))),
            'uploader_url': f'https://www.threads.com/@{username}',
            'timestamp': int_or_none(post_data.get('taken_at')),
            'like_count': int_or_none(traverse_obj(post_data, ('like_count',))),
            'view_count': int_or_none(traverse_obj(post_data, ('view_count',))),
            'comment_count': int_or_none(traverse_obj(post_data, ('reply_count',))),
            'repost_count': int_or_none(traverse_obj(post_data, ('repost_count',))),
            'quote_count': int_or_none(traverse_obj(post_data, ('quote_count',))),
        }

    def _real_extract(self, url):
        username, post_id = self._match_valid_url(url).group('username', 'id')

        # Fetch post data from Threads
        result = self._fetch_post_data(url, post_id)

        # If _fetch_post_data returned a complete info dict (Open Graph fallback), return it directly
        if 'formats' in result and isinstance(result.get('formats'), list):
            return result

        # If _fetch_post_data returned a playlist (carousel from HTML), return it directly
        if result.get('_type') == 'playlist':
            return result

        # Extract media formats (video/image)
        media_result = self._extract_media_formats(result, post_id)

        # If it's a carousel playlist, return it directly
        if media_result.get('_type') == 'playlist':
            playlist_title = media_result.get('title', f'Post by {username}')
            metadata = self._extract_metadata(result, username)
            return {
                '_type': 'playlist',
                'id': post_id,
                'title': playlist_title,
                'entries': media_result.get('entries', []),
                **metadata,
            }

        # Extract metadata
        metadata = self._extract_metadata(result, username)

        # Check if it's a text-only post (no media)
        if not media_result.get('formats'):
            raise ExtractorError('This post does not contain any downloadable media', expected=True)

        return {
            'id': post_id,
            'formats': media_result.get('formats', []),
            'thumbnails': media_result.get('thumbnails', []),
            'http_headers': {
                'Referer': 'https://www.threads.net/',
            },
            **metadata,
        }
