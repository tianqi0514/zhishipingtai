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
