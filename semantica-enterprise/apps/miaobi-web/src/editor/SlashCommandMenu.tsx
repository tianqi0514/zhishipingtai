import {
  Combobox,
  ComboboxGroup,
  ComboboxGroupLabel,
  ComboboxItem,
  ComboboxPopover,
  ComboboxProvider,
  Portal,
  useComboboxContext,
  useComboboxStore,
} from '@ariakit/react';
import { useComboboxInput, useHTMLInputCursorState } from '@platejs/combobox/react';
import { insertCallout } from '@platejs/callout';
import { insertCodeBlock } from '@platejs/code-block';
import { insertDate } from '@platejs/date';
import { insertFootnote } from '@platejs/footnote';
import { insertColumnGroup } from '@platejs/layout';
import { insertEquation, insertInlineEquation } from '@platejs/math';
import { TablePlugin } from '@platejs/table/react';
import { insertToc } from '@platejs/toc';
import {
  BookOpenCheck,
  CalendarDays,
  Calculator,
  CheckSquare,
  Columns3,
  FileText,
  Heading1,
  Heading2,
  Heading3,
  ImagePlus,
  Lightbulb,
  List,
  ListOrdered,
  MessageSquareText,
  Pilcrow,
  Quote,
  Radical,
  Sparkles,
  Table2,
  Workflow,
} from 'lucide-react';
import { KEYS, type PointRef, type TComboboxInputElement } from 'platejs';
import {
  PlateElement,
  type PlateEditor,
  type PlateElementProps,
  useEditorRef,
} from 'platejs/react';
import { createContext, startTransition, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

export type MiaobiEditorCommand = 'assistant' | 'evidence' | 'calculation' | 'inference' | 'image';

export function requestMiaobiEditorCommand(command: MiaobiEditorCommand) {
  window.dispatchEvent(new CustomEvent('miaobi:editor-command', { detail: { command } }));
}

type SlashItem = {
  value: string;
  label: string;
  description: string;
  keywords: string[];
  icon: ReactNode;
  run: (editor: PlateEditor) => void;
};

type SlashGroup = { label: string; items: SlashItem[] };

function setCurrentBlock(editor: PlateEditor, type: string) {
  editor.tf.setNodes({ type, indent: undefined, listStyleType: undefined });
  editor.tf.focus();
}

function setCurrentList(editor: PlateEditor, listStyleType: string) {
  editor.tf.setNodes({ type: KEYS.p, indent: 1, listStyleType });
  editor.tf.focus();
}

function runCommand(editor: PlateEditor, command: MiaobiEditorCommand) {
  requestMiaobiEditorCommand(command);
  editor.tf.focus();
}

const GROUPS: SlashGroup[] = [
  {
    label: '妙笔与知识',
    items: [
      { value: 'assistant', label: '妙笔助手', description: '用当前知识版本生成或修订内容', keywords: ['ai', '写作', '生成'], icon: <Sparkles />, run: (editor) => runCommand(editor, 'assistant') },
      { value: 'evidence', label: '插入知识依据', description: '从当前知识产品版本检索并绑定来源', keywords: ['引用', '知识', 'citation'], icon: <BookOpenCheck />, run: (editor) => runCommand(editor, 'evidence') },
    ],
  },
  {
    label: '基础内容',
    items: [
      { value: KEYS.p, label: '正文', description: '普通正文段落', keywords: ['paragraph', 'text'], icon: <Pilcrow />, run: (editor) => setCurrentBlock(editor, KEYS.p) },
      { value: KEYS.h1, label: '一级标题', description: '文稿主标题', keywords: ['heading', 'h1'], icon: <Heading1 />, run: (editor) => setCurrentBlock(editor, KEYS.h1) },
      { value: KEYS.h2, label: '二级标题', description: '章节标题', keywords: ['heading', 'h2'], icon: <Heading2 />, run: (editor) => setCurrentBlock(editor, KEYS.h2) },
      { value: KEYS.h3, label: '三级标题', description: '小节标题', keywords: ['heading', 'h3'], icon: <Heading3 />, run: (editor) => setCurrentBlock(editor, KEYS.h3) },
      { value: 'unordered-list', label: '无序列表', description: '创建项目符号列表', keywords: ['ul', 'bullet'], icon: <List />, run: (editor) => setCurrentList(editor, 'disc') },
      { value: 'ordered-list', label: '有序列表', description: '创建编号列表', keywords: ['ol', 'number'], icon: <ListOrdered />, run: (editor) => setCurrentList(editor, 'decimal') },
      { value: 'todo-list', label: '待办列表', description: '创建可勾选任务项', keywords: ['todo', 'task'], icon: <CheckSquare />, run: (editor) => setCurrentList(editor, 'todo') },
      { value: KEYS.blockquote, label: '引用段落', description: '突出显示引用或摘录', keywords: ['quote'], icon: <Quote />, run: (editor) => setCurrentBlock(editor, KEYS.blockquote) },
      { value: KEYS.callout, label: '提示块', description: '插入重点提示', keywords: ['callout', 'note'], icon: <Lightbulb />, run: (editor) => insertCallout(editor, { select: true }) },
    ],
  },
  {
    label: '结构与专业内容',
    items: [
      { value: KEYS.table, label: '表格', description: '插入 3×3 表格', keywords: ['table'], icon: <Table2 />, run: (editor) => editor.getTransforms(TablePlugin).insert.table({ colCount: 3, rowCount: 3 }, { select: true }) },
      { value: 'three-columns', label: '三栏布局', description: '插入三栏内容区', keywords: ['column'], icon: <Columns3 />, run: (editor) => insertColumnGroup(editor, { columns: 3, select: true }) },
      { value: KEYS.toc, label: '目录', description: '按标题生成文稿目录', keywords: ['toc'], icon: <FileText />, run: (editor) => insertToc(editor, { select: true }) },
      { value: KEYS.codeBlock, label: '代码块', description: '插入带格式的代码内容', keywords: ['code'], icon: <MessageSquareText />, run: (editor) => insertCodeBlock(editor, { select: true }) },
      { value: KEYS.date, label: '日期', description: '插入当前日期', keywords: ['date', 'time'], icon: <CalendarDays />, run: (editor) => insertDate(editor, { select: true }) },
      { value: KEYS.equation, label: '公式', description: '插入块级数学公式', keywords: ['math'], icon: <Radical />, run: (editor) => insertEquation(editor, { select: true }) },
      { value: 'inline-equation', label: '行内公式', description: '在当前句子中插入公式', keywords: ['math', 'inline'], icon: <Radical />, run: (editor) => insertInlineEquation(editor, '', { select: true }) },
      { value: 'footnote', label: '脚注', description: '插入脚注引用', keywords: ['footnote'], icon: <FileText />, run: (editor) => insertFootnote(editor, { select: true }) },
      { value: 'image', label: '图片', description: '通过安全地址插入图片', keywords: ['image', 'media'], icon: <ImagePlus />, run: (editor) => runCommand(editor, 'image') },
    ],
  },
];

type ComboboxContextValue = {
  inputProps: ReturnType<typeof useComboboxInput>['props'];
  inputRef: React.RefObject<HTMLInputElement | null>;
  removeInput: ReturnType<typeof useComboboxInput>['removeInput'];
};

const SlashContext = createContext<ComboboxContextValue | null>(null);

export function SlashInputElement(props: PlateElementProps<TComboboxInputElement>) {
  const { editor, element } = props;
  const inputRef = useRef<HTMLInputElement>(null);
  const cursorState = useHTMLInputCursorState(inputRef);
  const insertPointRef = useRef<PointRef | null>(null);
  const [query, setQuery] = useState('');

  useEffect(() => {
    const path = editor.api.findPath(element);
    const point = path ? editor.api.before(path) : undefined;
    if (!point) return;
    const pointRef = editor.api.pointRef(point);
    insertPointRef.current = pointRef;
    return () => {
      insertPointRef.current = null;
      pointRef.unref();
    };
  }, [editor, element]);

  const { props: inputProps, removeInput } = useComboboxInput({
    autoFocus: true,
    cancelInputOnBlur: true,
    cursorState,
    ref: inputRef,
    onCancelInput: (cause) => {
      if (cause !== 'backspace') editor.tf.insertText(`/${query}`, { at: insertPointRef.current?.current ?? undefined });
    },
  });
  const store = useComboboxStore({ setValue: (value) => startTransition(() => setQuery(value)) });
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return GROUPS;
    return GROUPS.map((group) => ({
      ...group,
      items: group.items.filter((item) => [item.label, item.description, item.value, ...item.keywords].join(' ').toLowerCase().includes(needle)),
    })).filter((group) => group.items.length);
  }, [query]);

  const select = useCallback((item: SlashItem) => {
    removeInput(false);
    window.setTimeout(() => item.run(editor), 0);
  }, [editor, removeInput]);

  return (
    <PlateElement {...props} as="span" className="slash-input-node">
      <span contentEditable={false}>
        <ComboboxProvider open store={store}>
          <span className="slash-input-inline">/<SlashSearchInput ref={inputRef} inputProps={inputProps} /></span>
          <Portal>
            <SlashPopover gutter={8}>
              <div className="slash-command-head"><b>插入内容</b><span>输入关键词筛选</span></div>
              {filtered.map((group) => (
                <ComboboxGroup key={group.label} className="slash-command-group">
                  <ComboboxGroupLabel>{group.label}</ComboboxGroupLabel>
                  {group.items.map((item) => (
                    <ComboboxItem
                      key={item.value}
                      value={item.value}
                      className="slash-command-item"
                      onClick={() => select(item)}
                    >
                      <span className="slash-command-icon">{item.icon}</span>
                      <span><b>{item.label}</b><small>{item.description}</small></span>
                    </ComboboxItem>
                  ))}
                </ComboboxGroup>
              ))}
              {!filtered.length && <div className="slash-command-empty">没有匹配的指令</div>}
            </SlashPopover>
          </Portal>
        </ComboboxProvider>
      </span>
      {props.children}
    </PlateElement>
  );
}

function SlashSearchInput({ inputProps, ref }: { inputProps: ReturnType<typeof useComboboxInput>['props']; ref: React.RefObject<HTMLInputElement | null> }) {
  return <Combobox {...inputProps} ref={ref} aria-label="搜索插入指令" className="slash-search-input" />;
}

function SlashPopover(props: React.ComponentProps<typeof ComboboxPopover>) {
  const store = useComboboxContext();
  return <ComboboxPopover {...props} store={store || undefined} className="slash-command-popover" />;
}
