const DIRECT_RESPONSE_PATTERNS = [
  /^(你是谁|你是什么(?:助手|模型)?|你叫什么(?:名字)?|请?介绍(?:一下)?你自己|自我介绍)[？?。！!\s]*$/u,
  /^(你好|您好|嗨|哈[喽啰]|hello|hi|hey|在吗|早上好|上午好|下午好|晚上好)[？?。！!\s]*$/iu,
  /^(谢谢|感谢|多谢|辛苦了|再见|拜拜|bye)[你您了啊呀哈？?。！!\s]*$/iu,
  /^(帮助|help|你能做什么|你会什么|怎么用你|如何使用你|使用帮助|有什么功能)[？?。！!\s]*$/iu,
]


export function requiresKnowledgeSearch(input) {
  const query = String(input || '').trim()
  if (!query) return false
  return !DIRECT_RESPONSE_PATTERNS.some(pattern => pattern.test(query))
}


const STRUCTURED_QUERY_PATTERNS = [
  /(?:多少|金额|总额|数量|销量|销售额|排名|最高|最低|平均|合计|统计|同比|环比|增长率|完成率|top\s*\d*|前\s*\d+|实时状态)/iu,
  /(?:sum|count|average|ranking|year[- ]over[- ]year|month[- ]over[- ]month)/iu,
]

const DOCUMENT_EVIDENCE_PATTERNS = [
  /(?:口径|依据|制度|规定|定义|说明|手册|合同|条款|政策|文档)/u,
]

export function requiresStructuredQuery(input) {
  const query = String(input || '').trim()
  if (!query || !requiresKnowledgeSearch(query)) return false
  return STRUCTURED_QUERY_PATTERNS.some(pattern => pattern.test(query))
}


export function evidenceRequirements(input) {
  const query = String(input || '').trim()
  if (!requiresKnowledgeSearch(query)) return []
  const structured = requiresStructuredQuery(query)
  const document = !structured || DOCUMENT_EVIDENCE_PATTERNS.some(pattern => pattern.test(query))
  return [
    ...(document ? ['knowledge_search'] : []),
    ...(structured ? ['structured_execute_query'] : []),
  ]
}


export function currentUserQuery(events) {
  const message = [...(events || [])].reverse().find(event => (
    event?.type === 'user/message'
    && event?.data?.source?.kind === 'user'
  ))
  const content = message?.data?.content
  if (!Array.isArray(content)) return ''
  return content
    .filter(block => block?.type === 'text')
    .map(block => String(block.text || ''))
    .join('')
    .trim()
}
