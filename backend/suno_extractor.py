import re
import json
import logging
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

logger = logging.getLogger("mp3metafix.suno")

# UUIDv4 Pattern
UUID_REGEX = re.compile(r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", re.IGNORECASE)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


def extract_suno_id(input_str: Optional[str]) -> Optional[str]:
    """Extracts a valid Suno Clip UUID from a URL, comment string, or raw UUID."""
    if not input_str or not isinstance(input_str, str):
        return None
    
    match = UUID_REGEX.search(input_str.strip())
    if match:
        return match.group(1).lower()
    return None


def unescape_nextjs_chunk(raw_chunk: str) -> str:
    """Safely decodes Next.js serialized stream chunk without double-decoding UTF-8 characters."""
    if not raw_chunk:
        return ""
    try:
        # Standard JSON string parsing handles \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
        # Normalize any unescaped physical newlines/tabs inside string before json.loads
        normalized = raw_chunk.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
        return json.loads(f'"{normalized}"')
    except Exception:
        pass

    # Fallback to regex-based unicode and escape sequence replacement
    def replace_escape(match: re.Match) -> str:
        esc = match.group(0)
        if esc.startswith("\\u"):
            try:
                return chr(int(esc[2:], 16))
            except Exception:
                return esc
        elif esc == '\\"':
            return '"'
        elif esc == "\\\\":
            return "\\"
        elif esc == "\\n":
            return "\n"
        elif esc == "\\r":
            return "\r"
        elif esc == "\\t":
            return "\t"
        return esc[1:]

    return re.sub(r"\\(?:u[0-9a-fA-F]{4}|[\"\\/bfnrt])", replace_escape, raw_chunk)


def parse_next_f_payload(html: str) -> Dict[str, Any]:
    """Extracts and parses Next.js SSR stream payload chunks."""
    matches = re.findall(r"self\.__next_f\.push\(\[1,\"(.*?)\"\]\)", html)
    combined = ""
    for m in matches:
        try:
            combined += unescape_nextjs_chunk(m)
        except Exception as e:
            logger.debug(f"Chunk unescape error: {e}")
            continue
    return parse_suno_combined_stream(combined, html)


def parse_suno_combined_stream(combined: str, raw_html: str = "") -> Dict[str, Any]:
    """Parses combined Next.js stream string for clip metadata, lyrics, and artwork."""
    result: Dict[str, Any] = {
        "id": "",
        "title": "",
        "artist": "",
        "handle": "",
        "genre": "",
        "prompt": "",
        "lyrics": "",
        "created_at": "",
        "year": "",
        "image_url": "",
        "model": "",
        "formatted_comment": "",
    }

    # 1. Locate the "clip":{...} object
    clip_pos = combined.find('"clip":{')
    clip_dict: Dict[str, Any] = {}
    if clip_pos != -1:
        snippet = combined[clip_pos + 7:]
        brace_count = 0
        end_idx = 0
        for i, char in enumerate(snippet):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break
        if end_idx > 0:
            try:
                clip_dict = json.loads(snippet[:end_idx])
            except Exception as e:
                logger.warning(f"Error parsing clip JSON snippet: {e}")

    # Fallback to OpenGraph / Meta tags if clip dict is empty
    if not clip_dict and raw_html:
        og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']', raw_html, re.I)
        og_image = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\'](.*?)["\']', raw_html, re.I)
        if og_title:
            result["title"] = og_title.group(1).strip()
        if og_image:
            result["image_url"] = og_image.group(1).strip()

    if clip_dict:
        result["id"] = clip_dict.get("id", "")
        result["title"] = clip_dict.get("title", "")
        result["artist"] = clip_dict.get("display_name", "") or clip_dict.get("handle", "")
        result["handle"] = clip_dict.get("handle", "")
        result["created_at"] = clip_dict.get("created_at", "")
        result["image_url"] = clip_dict.get("image_large_url") or clip_dict.get("image_url", "")
        
        # Model
        major_ver = clip_dict.get("major_model_version", "")
        model_name = clip_dict.get("model_name", "")
        result["model"] = f"{major_ver} {model_name}".strip()

        meta = clip_dict.get("metadata") or {}
        if isinstance(meta, dict):
            result["genre"] = meta.get("tags", "")
            result["prompt"] = meta.get("prompt", "")

    # Extract Year from created_at or current date
    if result["created_at"]:
        year_match = re.search(r"(\d{4})", result["created_at"])
        if year_match:
            result["year"] = year_match.group(1)

    # 2. Extract structured lyrics from Next.js payload stream
    # In Suno Next.js SSR, lyrics appear in stream before clip or inside text tokens
    if clip_pos != -1:
        pre_clip = combined[:clip_pos]
        # Look for [Intro], [Verse], etc.
        lyric_match = re.search(r"(\[(?:Intro|Verse|Chorus|Bridge|Hook|Outro|Drop|Solo|Interlude|Build|Vamp|End)[^\]]*\][\s\S]*?)(?:41:\[|\[\$|$)", pre_clip, re.IGNORECASE)
        if lyric_match:
            raw_lyrics = lyric_match.group(1).strip()
            # Clean up Next.js stream artifacts if any
            clean_lyrics = re.sub(r"\d+:[a-zA-Z0-9_\-\$]+,", "", raw_lyrics).strip()
            result["lyrics"] = clean_lyrics

    # If lyrics not found in stream, check prompt for lyric structures
    if not result["lyrics"] and result["prompt"]:
        if any(marker in result["prompt"] for marker in ["[Verse", "[Chorus", "[Intro", "[Bridge"]):
            result["lyrics"] = result["prompt"]

    # 3. Build formatted comment
    prompt_val = result.get("prompt", "").strip()
    # Filter out React internal stream references like $51, $52, $undefined
    is_internal_ref = bool(re.match(r"^\$[0-9a-zA-Z_]+$", prompt_val)) or prompt_val in ["null", "undefined"]
    
    parts = []
    if prompt_val and not is_internal_ref:
        parts.append(f"Prompt: {prompt_val}")
    if result.get("genre"):
        parts.append(f"Style: {result['genre']}")
    if result.get("handle"):
        parts.append(f"Created on Suno.com (@{result['handle']})")
    elif result.get("id"):
        parts.append(f"Suno ID: {result['id']}")
    
    result["formatted_comment"] = " | ".join(parts) if parts else "Made with Suno"

    return result


def fetch_suno_metadata(clip_id_or_url: str, timeout: int = 10) -> Dict[str, Any]:
    """Fetches and parses metadata from Suno for a given Clip UUID or URL."""
    clip_id = extract_suno_id(clip_id_or_url)
    if not clip_id:
        raise ValueError("Invalid Suno Clip UUID or URL format.")

    url = f"https://suno.com/song/{clip_id}"
    from backend.outbound import fetch_public_bytes
    try:
        html_bytes = fetch_public_bytes(url, {'suno.com'}, 4 * 1024 * 1024, timeout)
        html_text = html_bytes.decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Suno song not found (Clip ID: {clip_id}).")
        raise ValueError(f"Failed to fetch Suno song (HTTP {e.code}).")
    except Exception as e:
        logger.error(f"Suno fetch error: {e}")
        raise ValueError(f"Could not connect to Suno: {str(e)}")

    metadata = parse_next_f_payload(html_text)
    if not metadata.get("id"):
        metadata["id"] = clip_id
    if not metadata.get("title"):
        raise ValueError("Unable to parse song information from Suno page.")
        
    return metadata
