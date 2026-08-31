EXTENSION_BY_DOCUMENT_TYPE = {
    "html": "html",
    "pdf": "pdf",
    "doc": "doc",
    "docx": "docx",
}

CONTENT_TYPE_BY_DOCUMENT_TYPE = {
    "html": "text/html",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
}

DOCUMENT_TYPE_BY_EXTENSION = {
    "html": "html",
    "htm": "html",
    "pdf": "pdf",
    "doc": "doc",
    "docx": "docx",
}

DOCUMENT_TYPE_BY_CONTENT_TYPE = {
    content_type: document_type
    for document_type, content_type in CONTENT_TYPE_BY_DOCUMENT_TYPE.items()
}
