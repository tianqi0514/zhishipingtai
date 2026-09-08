const entityMap: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#39;': "'",
  '&nbsp;': ' ',
};

export function cleanEvidenceText(value: unknown, maxLength = 420) {
  const decoded = String(value || '')
    .replace(/&(amp|lt|gt|quot|#39|nbsp);/gi, (entity) => entityMap[entity.toLowerCase()] || entity)
    .replace(/(^|[\s：])#{1,6}\s+/gm, '$1')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/(?:\*\*|__|`)(.*?)(?:\*\*|__|`)/g, '$1')
    .replace(/\[(.*?)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
  if (decoded.length <= maxLength) return decoded;
  return `${decoded.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}
