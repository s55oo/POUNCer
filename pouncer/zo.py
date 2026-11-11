"""Three-column ZO layout; indexes always refer to the original spot list."""
GROUPS = ('urgent', 'medium', 'normal', 'unknown')
LABELS = ('⚡ Urgent', 'Medium', 'Normal', 'Unknown')


def group(value):
    return value if value in GROUPS else 'unknown'


def layout(items, width, card_height, gap=10, heading_height=28):
    """Return (boxes in original-index order, headings, total height)."""
    buckets = [[i for i, it in enumerate(items) if group(it.get('urgency')) == g] for g in GROUPS]
    cw = max(1, (width - gap * 4) / 3)
    boxes = [None] * len(items)
    headings = []
    for col in range(3):
        count = len(buckets[col]) + (len(buckets[3]) if col == 2 else 0)
        headings.append((gap + col * (cw + gap), gap, f'{LABELS[col]} ({count})'))
        y = gap + heading_height
        for index in buckets[col]:
            x = gap + col * (cw + gap)
            boxes[index] = ((x, y, x + cw, y + card_height), index)
            y += card_height + gap
        if col == 2 and buckets[3]:
            headings.append((gap + col * (cw + gap), y, f'Unknown ({len(buckets[3])})'))
            y += heading_height
            for index in buckets[3]:
                x = gap + col * (cw + gap)
                boxes[index] = ((x, y, x + cw, y + card_height), index)
                y += card_height + gap
    height = max((box[0][3] + gap for box in boxes), default=gap + heading_height)
    return boxes, headings, height


def neighbor(boxes, selected, dx, dy):
    if not boxes:
        return None
    if selected is None:
        return min(boxes, key=lambda b: (b[0][0], b[0][1]))[1]
    here = boxes[selected][0]
    x, y = here[0], here[1]
    choices = [b for b in boxes if (dx and (b[0][0] - x) * dx > 1)
               or (dy and abs(b[0][0] - x) < 1 and (b[0][1] - y) * dy > 1)]
    if not choices:
        return selected
    return min(choices, key=lambda b: (abs(b[0][0] - x), abs(b[0][1] - y)))[1]
