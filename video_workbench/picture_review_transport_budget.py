"""Admission bound for the retained Worker's unchanged native JSONL lane."""
import json

from agent_service.contracts import ServiceError

NATIVE_EVENT_MAX_BYTES = 4*1024*1024
NATIVE_ENVELOPE_RESERVE_BYTES = 64*1024


def require_native_wire_budget(input_name, files):
    """Count base64 images and escaped task text before upload or job creation.

    Profile limits describe retained raw inputs. This separate conservative
    bound describes their combined read_inputs tool result and event wrapper.
    Passing is a transport admission check, never a model-observation receipt.
    The gateway validates the exact file manifest and all hashes first.
    """
    if (type(files) is not dict or input_name not in files
            or any(type(raw) is not bytes for raw in files.values())):
        raise ServiceError('picture_review_native_wire_input_invalid', 422)
    try:
        text = files[input_name].decode('utf-8')
    except UnicodeError:
        raise ServiceError('picture_review_native_wire_input_invalid', 422) from None
    escaped_text_bytes = len(json.dumps(text, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    image_base64_bytes = sum(4*((len(raw)+2)//3) for name, raw in files.items() if name != input_name)
    bound = escaped_text_bytes + image_base64_bytes + NATIVE_ENVELOPE_RESERVE_BYTES
    if bound > NATIVE_EVENT_MAX_BYTES:
        raise ServiceError('picture_review_native_wire_too_large', 413)
    return bound
