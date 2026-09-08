import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BlockquotePlugin,
  BoldPlugin,
  H1Plugin,
  H2Plugin,
  H3Plugin,
  HighlightPlugin,
  ItalicPlugin,
  StrikethroughPlugin,
  UnderlinePlugin,
} from '@platejs/basic-nodes/react';
import { CalloutPlugin } from '@platejs/callout/react';
import { CodeBlockPlugin, CodeLinePlugin } from '@platejs/code-block/react';
import { DatePlugin } from '@platejs/date/react';
import { ColumnItemPlugin, ColumnPlugin } from '@platejs/layout/react';
import { LinkPlugin } from '@platejs/link/react';
import { BaseListPlugin } from '@platejs/list';
import { EquationPlugin, InlineEquationPlugin } from '@platejs/math/react';
import { ImagePlugin } from '@platejs/media/react';
import { MentionPlugin } from '@platejs/mention/react';
import {
  TableCellHeaderPlugin,
  TableCellPlugin,
  TablePlugin,
  TableRowPlugin,
} from '@platejs/table/react';
import { TocPlugin } from '@platejs/toc/react';
import { KEYS, nanoid, type Value } from 'platejs';
import {
  ParagraphPlugin,
  Plate,
  PlateContent,
  PlateElement,
  PlateLeaf,
  type PlateElementProps,
  type PlateLeafProps,
  usePlateEditor,
} from 'platejs/react';
import { api } from '../api';
import type { PlateNode, WritingDocument } from '../types/domain';
import { TrustedBlockKit, trustedBlockTypes } from './plugins/trusted-blocks';

const Element = ({ children, ...props }: PlateElementProps) => <PlateElement {...props}>{children}</PlateElement>;
const H1 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h1">{children}</PlateElement>;
const H2 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h2">{children}</PlateElement>;
const H3 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h3">{children}</PlateElement>;
const Quote = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="blockquote">{children}</PlateElement>;
const Strong = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="strong">{children}</PlateLeaf>;
const Emphasis = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="em">{children}</PlateLeaf>;
const Underline = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="u">{children}</PlateLeaf>;
const Strike = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="s">{children}</PlateLeaf>;
const Highlight = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="mark">{children}</PlateLeaf>;
const Table = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="table"><tbody>{children}</tbody></PlateElement>;
const TableRow = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="tr">{children}</PlateElement>;
const TableCell = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="td">{children}</PlateElement>;
const TableHeader = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="th">{children}</PlateElement>;
const Link = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="a" attributes={{ ...props.attributes, href: String((props.element as Record<string, unknown>).url || '#') }}>{children}</PlateElement>;
const Image = ({ children, ...props }: PlateElementProps) => <PlateElement {...props}><img src={String((props.element as Record<string, unknown>).url || '')} alt="文稿图片" contentEditable={false} />{children}</PlateElement>;

export const EditorKit = [
  ParagraphPlugin.withComponent(Element),
  H1Plugin.withComponent(H1), H2Plugin.withComponent(H2), H3Plugin.withComponent(H3),
  BlockquotePlugin.withComponent(Quote),
  BoldPlugin.withComponent(Strong), ItalicPlugin.withComponent(Emphasis),
  UnderlinePlugin.withComponent(Underline), StrikethroughPlugin.withComponent(Strike),
  HighlightPlugin.withComponent(Highlight),
  LinkPlugin.withComponent(Link),
  TablePlugin.withComponent(Table), TableRowPlugin.withComponent(TableRow),
  TableCellPlugin.withComponent(TableCell), TableCellHeaderPlugin.withComponent(TableHeader),
  CalloutPlugin.withComponent(Element), ColumnPlugin.withComponent(Element), ColumnItemPlugin.withComponent(Element),
  CodeBlockPlugin.withComponent(Element), CodeLinePlugin.withComponent(Element),
  DatePlugin.withComponent(Element), EquationPlugin.withComponent(Element), InlineEquationPlugin.withComponent(Element),
  ImagePlugin.configure({ options: { disableUploadInsert: true }, node: { component: Image } }),
  MentionPlugin.withComponent(Element), TocPlugin.withComponent(Element), BaseListPlugin,
  ...TrustedBlockKit,
];

const EMPTY_VALUE: Value = [
  { id: 'title', type: KEYS.h1, children: [{ text: '应急处置方案' }] },
  { id: 'intro', type: KEYS.p, children: [{ text: '请从左侧目录选择章节，或使用右侧妙笔助手生成有依据的草稿。' }] },
];

type Props = {
  document: WritingDocument;
  onSaved: (document: WritingDocument) => void;
  onDirtyChange: (dirty: boolean) => void;
  insertionRequest?: PlateNode | null;
  onInserted?: () => void;
};

export function MiaobiEditor({ document, onSaved, onDirtyChange, insertionRequest, onInserted }: Props) {
  const initial = (document.current_version?.content?.length ? document.current_version.content : EMPTY_VALUE) as Value;
  const editor = usePlateEditor({ plugins: EditorKit, value: initial }, [document.id]);
  const [saving, setSaving] = useState(false);
  const [lastSavedHash, setLastSavedHash] = useState(document.current_version?.content_hash || '');
  const valueRef = useRef<Value>(initial);
  const saveTimer = useRef<number | undefined>(undefined);

  const save = async (summary = '自动保存') => {
    if (saving) return;
    setSaving(true);
    try {
      const version = await api<Record<string, unknown>>(`/writing/documents/${document.id}/versions`, {
        method: 'POST',
        body: { content: valueRef.current, change_summary: summary, publish: false },
      });
      setLastSavedHash(String(version.content_hash || ''));
      onDirtyChange(false);
      onSaved({ ...document, current_version: version as WritingDocument['current_version'] });
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  useEffect(() => {
    if (!insertionRequest) return;
    editor.tf.insertNodes(insertionRequest as Value[number]);
    onInserted?.();
  }, [editor, insertionRequest, onInserted]);

  const tools = useMemo(() => [
    ['正文', KEYS.p], ['标题1', KEYS.h1], ['标题2', KEYS.h2], ['标题3', KEYS.h3],
  ] as const, []);

  const insertBlock = (type: string, text: string) => {
    editor.tf.insertNodes({
      id: nanoid(),
      type,
      freshness_status: trustedBlockTypes.includes(type) ? 'unverified' : undefined,
      children: [{ text }],
    });
  };

  const insertTable = () => editor.tf.insertNodes({
    id: nanoid(), type: KEYS.table, children: [
      { type: KEYS.tr, children: [
        { type: KEYS.th, children: [{ type: KEYS.p, children: [{ text: '项目' }] }] },
        { type: KEYS.th, children: [{ type: KEYS.p, children: [{ text: '内容' }] }] },
      ] },
      { type: KEYS.tr, children: [
        { type: KEYS.td, children: [{ type: KEYS.p, children: [{ text: '待填写' }] }] },
        { type: KEYS.td, children: [{ type: KEYS.p, children: [{ text: '待填写' }] }] },
      ] },
    ],
  });

  return (
    <div className="editor-shell" data-testid="plate-editor">
      <div className="editor-toolbar" role="toolbar" aria-label="文稿编辑工具">
        <button type="button" onClick={() => editor.tf.undo()} title="撤销">↶</button>
        <button type="button" onClick={() => editor.tf.redo()} title="重做">↷</button>
        <span className="toolbar-divider" />
        {tools.map(([label, type]) => <button type="button" key={type} onClick={() => editor.tf.setNodes({ type })}>{label}</button>)}
        <span className="toolbar-divider" />
        <button type="button" onClick={() => editor.tf.toggleMark(KEYS.bold)}><b>B</b></button>
        <button type="button" onClick={() => editor.tf.toggleMark(KEYS.italic)}><i>I</i></button>
        <button type="button" onClick={() => editor.tf.toggleMark(KEYS.underline)}><u>U</u></button>
        <button type="button" onClick={() => insertTable()}>表格</button>
        <button type="button" onClick={() => insertBlock('knowledge_citation', '待选择知识来源')}>知识引用</button>
        <button type="button" onClick={() => insertBlock('computed_metric', '待插入测算结果')}>测算值</button>
        <button type="button" onClick={() => insertBlock('inference_conclusion', '待插入推演结论')}>推演结论</button>
        <button type="button" className="save-button" disabled={saving} onClick={() => void save('手工保存')}>
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
      <div className="editor-page-wrap">
        <Plate
          editor={editor}
          onChange={({ value }) => {
            valueRef.current = value;
            onDirtyChange(true);
            window.clearTimeout(saveTimer.current);
            saveTimer.current = window.setTimeout(() => void save(), 1800);
          }}
        >
          <PlateContent className="editor-page" placeholder="开始撰写方案……" />
        </Plate>
      </div>
      <div className="editor-status"><span>{saving ? '正在安全保存' : lastSavedHash ? '已保存' : '尚未保存'}</span><span>Plate 53.3.11 · 可信块校验已启用</span></div>
    </div>
  );
}
