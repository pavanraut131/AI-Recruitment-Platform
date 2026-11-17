"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  BriefcaseIcon,
  CopyIcon,
  DownloadIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  PlusIcon,
  SettingsIcon,
  Trash2Icon,
  UploadIcon,
  UsersIcon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

/* ------------------------------------------------------------------------ */
/* Layout                                                                    */
/* ------------------------------------------------------------------------ */

export function ComponentGallery() {
  return (
    <div className="flex flex-col gap-6 px-6 py-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">UI gallery</h1>
        <p className="max-w-3xl text-muted-foreground">
          Every primitive in <code className="font-mono">src/components/ui</code>{" "}
          in each state, rendered in a light and a dark pane. Overlays
          (dialog, sheet, popover, menus, tooltips, toasts) open in a portal and
          follow the app theme: switch it from the sidebar or with ⌘K. Development
          builds only.
        </p>
      </header>
      <div className="grid gap-6 2xl:grid-cols-2">
        <ThemePane theme="light" />
        <ThemePane theme="dark" />
      </div>
    </div>
  )
}

function ThemePane({ theme }: { theme: "light" | "dark" }) {
  return (
    <section
      aria-label={`${theme === "light" ? "Light" : "Dark"} theme`}
      className={cn(
        theme,
        "flex min-w-0 flex-col gap-10 rounded-xl border bg-background p-6 text-foreground"
      )}
    >
      <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
        {theme} theme
      </p>
      <ColorsSection />
      <TypographySection />
      <ButtonsSection />
      <BadgesSection />
      <ScoreBarsSection />
      <InputsSection />
      <SelectSection />
      <CheckboxSection />
      <TabsSection />
      <TableSection />
      <CardSection />
      <SkeletonSection />
      <MiscSection />
      <CommandSection />
      <OverlaysSection />
      <ToastsSection />
      <FormSection />
    </section>
  )
}

function Section({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-base font-semibold">{title}</h2>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      {children}
    </div>
  )
}

function Row({
  label,
  children,
  className,
}: {
  label?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className="flex flex-col gap-2">
      {label && <span className="text-xs text-muted-foreground">{label}</span>}
      <div className={cn("flex flex-wrap items-center gap-3", className)}>
        {children}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------------ */
/* Foundations                                                               */
/* ------------------------------------------------------------------------ */

// Literal class names so Tailwind generates them.
const swatches: { name: string; className: string }[] = [
  { name: "background", className: "bg-background" },
  { name: "card", className: "bg-card" },
  { name: "popover", className: "bg-popover" },
  { name: "muted", className: "bg-muted" },
  { name: "secondary", className: "bg-secondary" },
  { name: "accent", className: "bg-accent" },
  { name: "primary", className: "bg-primary" },
  { name: "ring", className: "bg-ring" },
  { name: "destructive", className: "bg-destructive" },
  { name: "border", className: "bg-border" },
  { name: "input", className: "bg-input" },
  { name: "sidebar", className: "bg-sidebar" },
  { name: "chart-1", className: "bg-chart-1" },
  { name: "chart-2", className: "bg-chart-2" },
  { name: "chart-3", className: "bg-chart-3" },
  { name: "chart-4", className: "bg-chart-4" },
  { name: "chart-5", className: "bg-chart-5" },
]

const categories = [
  {
    name: "strong",
    solid: "bg-category-strong",
    muted: "bg-category-strong-muted",
    text: "text-category-strong-foreground",
  },
  {
    name: "moderate",
    solid: "bg-category-moderate",
    muted: "bg-category-moderate-muted",
    text: "text-category-moderate-foreground",
  },
  {
    name: "weak",
    solid: "bg-category-weak",
    muted: "bg-category-weak-muted",
    text: "text-category-weak-foreground",
  },
  {
    name: "rejected",
    solid: "bg-category-rejected",
    muted: "bg-category-rejected-muted",
    text: "text-category-rejected-foreground",
  },
] as const

function ColorsSection() {
  return (
    <Section
      title="Colour tokens"
      description="src/styles/tokens.css: shadcn convention plus --category-*. No hex in components."
    >
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        {swatches.map((swatch) => (
          <div key={swatch.name} className="flex flex-col gap-1.5">
            <div className={cn("h-10 rounded-md border", swatch.className)} />
            <span className="font-mono text-[11px] text-muted-foreground">
              {swatch.name}
            </span>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {categories.map((category) => (
          <div key={category.name} className="flex flex-col gap-1.5">
            <div className="flex h-10 overflow-hidden rounded-md border">
              <div className={cn("w-1/3", category.solid)} />
              <div
                className={cn(
                  "flex flex-1 items-center justify-center text-xs font-medium",
                  category.muted,
                  category.text
                )}
              >
                Aa
              </div>
            </div>
            <span className="font-mono text-[11px] text-muted-foreground">
              category-{category.name}
            </span>
          </div>
        ))}
      </div>
    </Section>
  )
}

function TypographySection() {
  return (
    <Section title="Typography" description="Geist Sans / Geist Mono via next/font.">
      <div className="flex flex-col gap-2">
        <p className="text-2xl font-semibold tracking-tight">Page title 24px</p>
        <p className="text-xl font-semibold tracking-tight">Section title 20px</p>
        <p className="text-[15px]">Body 15px: calm, data-dense, pitch-ready.</p>
        <p className="text-sm">Body 14px (default): the review panel and forms.</p>
        <p className="text-dense">Table 13px (text-dense): rows in the candidates table.</p>
        <p className="text-sm text-muted-foreground">Muted 14px: secondary text.</p>
        <p className="font-mono text-sm">Mono: resume_id 4f9c2a1e</p>
        <p className="text-sm">
          <a href="#" className="text-primary underline-offset-4 hover:underline">
            Link text
          </a>
        </p>
        <div className="flex gap-6 text-sm">
          <div className="numeric flex flex-col items-end">
            <span>92.5</span>
            <span>81.0</span>
            <span>7.25</span>
            <span className="text-xs text-muted-foreground">numeric</span>
          </div>
          <div className="flex flex-col items-end">
            <span>92.5</span>
            <span>81.0</span>
            <span>7.25</span>
            <span className="text-xs text-muted-foreground">proportional</span>
          </div>
        </div>
      </div>
    </Section>
  )
}

/* ------------------------------------------------------------------------ */
/* Primitives                                                                */
/* ------------------------------------------------------------------------ */

const buttonVariants = [
  "default",
  "secondary",
  "outline",
  "ghost",
  "destructive",
  "link",
] as const

function ButtonsSection() {
  return (
    <Section title="Button" description="Tab through to see the teal focus ring.">
      <Row label="Variants">
        {buttonVariants.map((variant) => (
          <Button key={variant} variant={variant}>
            {variant.charAt(0).toUpperCase() + variant.slice(1)}
          </Button>
        ))}
      </Row>
      <Row label="Disabled">
        {buttonVariants.map((variant) => (
          <Button key={variant} variant={variant} disabled>
            {variant.charAt(0).toUpperCase() + variant.slice(1)}
          </Button>
        ))}
      </Row>
      <Row label="Sizes">
        <Button size="xs">Extra small</Button>
        <Button size="sm">Small</Button>
        <Button>Default</Button>
        <Button size="lg">Large</Button>
      </Row>
      <Row label="With icon, icon only, loading">
        <Button>
          <PlusIcon data-icon="inline-start" />
          New campaign
        </Button>
        <Button variant="outline">
          <UploadIcon data-icon="inline-start" />
          Upload
        </Button>
        <Button variant="outline" size="icon" aria-label="Settings">
          <SettingsIcon />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="More">
          <MoreHorizontalIcon />
        </Button>
        <Button disabled>
          <Loader2Icon className="animate-spin" data-icon="inline-start" />
          Saving…
        </Button>
      </Row>
    </Section>
  )
}

function BadgesSection() {
  return (
    <Section title="Badge" description="Category badges use the muted tint plus foreground tokens.">
      <Row label="Variants">
        <Badge>Default</Badge>
        <Badge variant="secondary">Secondary</Badge>
        <Badge variant="outline">Outline</Badge>
        <Badge variant="destructive">Destructive</Badge>
        <Badge variant="ghost">Ghost</Badge>
      </Row>
      <Row label="Categories">
        <Badge variant="strong">Strong</Badge>
        <Badge variant="moderate">Moderate</Badge>
        <Badge variant="weak">Weak</Badge>
        <Badge variant="rejected">Rejected</Badge>
        <Badge variant="outline">Pending</Badge>
      </Row>
    </Section>
  )
}

const scores = [
  { score: 91, bar: "w-[91%] bg-category-strong", badge: "strong" },
  { score: 68, bar: "w-[68%] bg-category-moderate", badge: "moderate" },
  { score: 42, bar: "w-[42%] bg-category-weak", badge: "weak" },
  { score: 12, bar: "w-[12%] bg-category-rejected", badge: "rejected" },
] as const

function ScoreBarsSection() {
  return (
    <Section
      title="Score bar (pattern)"
      description="Not a primitive: how E1-05 combines numeric text, a thin category bar and a badge."
    >
      <div className="flex max-w-sm flex-col gap-2">
        {scores.map(({ score, bar, badge }) => (
          <div key={score} className="flex items-center gap-3 text-dense">
            <span className="numeric w-7 text-right font-medium">{score}</span>
            <div
              className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"
              role="meter"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={score}
              aria-label="Score"
            >
              <div className={cn("h-full rounded-full", bar)} />
            </div>
            <Badge variant={badge} className="w-20">
              {badge.charAt(0).toUpperCase() + badge.slice(1)}
            </Badge>
          </div>
        ))}
      </div>
    </Section>
  )
}

function InputsSection() {
  const id = React.useId()
  return (
    <Section title="Input and textarea">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-default`}>Default</Label>
          <Input id={`${id}-default`} placeholder="Senior backend engineer" />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-filled`}>Filled</Label>
          <Input id={`${id}-filled`} defaultValue="Platform team hire, Q4" />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-disabled`}>Disabled</Label>
          <Input id={`${id}-disabled`} disabled defaultValue="Read only" />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-invalid`}>Invalid</Label>
          <Input
            id={`${id}-invalid`}
            aria-invalid
            aria-describedby={`${id}-invalid-msg`}
            defaultValue="x"
          />
          <p id={`${id}-invalid-msg`} className="text-xs text-destructive">
            Title must be at least 3 characters.
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-textarea`}>Textarea</Label>
          <Textarea id={`${id}-textarea`} placeholder="Paste a job description…" />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${id}-textarea-invalid`}>Textarea, invalid / disabled</Label>
          <Textarea id={`${id}-textarea-invalid`} aria-invalid defaultValue="Too short" />
          <Textarea disabled aria-label="Disabled textarea" defaultValue="Disabled" />
        </div>
      </div>
    </Section>
  )
}

function SelectSection() {
  return (
    <Section title="Select">
      <Row>
        <Select>
          <SelectTrigger aria-label="Status" className="w-40">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectLabel>Campaign status</SelectLabel>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="closed">Closed</SelectItem>
              <SelectItem value="archived" disabled>
                Archived (disabled)
              </SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <Select defaultValue="screening">
          <SelectTrigger aria-label="Stage" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="new">New</SelectItem>
            <SelectItem value="screening">Screening</SelectItem>
            <SelectItem value="interview">Interview</SelectItem>
          </SelectContent>
        </Select>
        <Select disabled>
          <SelectTrigger aria-label="Disabled" className="w-40">
            <SelectValue placeholder="Disabled" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="x">x</SelectItem>
          </SelectContent>
        </Select>
        <Select>
          <SelectTrigger aria-label="Invalid" aria-invalid className="w-40">
            <SelectValue placeholder="Invalid" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="x">x</SelectItem>
          </SelectContent>
        </Select>
        <Select>
          <SelectTrigger aria-label="Small" size="sm" className="w-32">
            <SelectValue placeholder="Small" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="x">x</SelectItem>
          </SelectContent>
        </Select>
      </Row>
    </Section>
  )
}

function CheckboxSection() {
  const id = React.useId()
  const states = [
    { key: "off", label: "Unchecked", props: {} },
    { key: "on", label: "Checked", props: { defaultChecked: true } },
    { key: "dis", label: "Disabled", props: { disabled: true } },
    { key: "dis-on", label: "Disabled, checked", props: { disabled: true, defaultChecked: true } },
    { key: "inv", label: "Invalid", props: { "aria-invalid": true } },
  ]
  return (
    <Section title="Checkbox">
      <Row className="gap-6">
        {states.map((state) => (
          <div key={state.key} className="flex items-center gap-2">
            <Checkbox id={`${id}-${state.key}`} {...state.props} />
            <Label htmlFor={`${id}-${state.key}`}>{state.label}</Label>
          </div>
        ))}
      </Row>
    </Section>
  )
}

function TabsSection() {
  return (
    <Section title="Tabs" description="Arrow keys move between tabs.">
      <Tabs defaultValue="candidates">
        <TabsList>
          <TabsTrigger value="candidates">Candidates</TabsTrigger>
          <TabsTrigger value="uploads">Uploads</TabsTrigger>
          <TabsTrigger value="insights">Insights</TabsTrigger>
          <TabsTrigger value="settings" disabled>
            Settings
          </TabsTrigger>
        </TabsList>
        <TabsContent value="candidates" className="text-muted-foreground">
          Ranked applications.
        </TabsContent>
        <TabsContent value="uploads" className="text-muted-foreground">
          Dropzone and processing list.
        </TabsContent>
        <TabsContent value="insights" className="text-muted-foreground">
          Charts.
        </TabsContent>
      </Tabs>
      <Tabs defaultValue="overview">
        <TabsList variant="line">
          <TabsTrigger value="overview">Line: overview</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>
      </Tabs>
    </Section>
  )
}

const rows = [
  { name: "Asha Verma", email: "asha@example.com", score: 91, badge: "strong", years: 7, selected: true },
  { name: "Daniel Okafor", email: "daniel@example.com", score: 68, badge: "moderate", years: 4, selected: false },
  { name: "Mei Lin", email: "mei@example.com", score: 42, badge: "weak", years: 2, selected: false },
] as const

function TableSection() {
  return (
    <Section title="Table" description="Selected row, numeric column, 13px dense text.">
      <div className="overflow-hidden rounded-lg border">
        <Table className="text-dense">
          <TableCaption className="pb-3">Sample applications</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">
                <Checkbox aria-label="Select all" />
              </TableHead>
              <TableHead>Name</TableHead>
              <TableHead className="text-right">Score</TableHead>
              <TableHead>Category</TableHead>
              <TableHead className="text-right">Experience</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.email} data-state={row.selected ? "selected" : undefined}>
                <TableCell>
                  <Checkbox aria-label={`Select ${row.name}`} defaultChecked={row.selected} />
                </TableCell>
                <TableCell>
                  <div className="font-medium">{row.name}</div>
                  <div className="text-muted-foreground">{row.email}</div>
                </TableCell>
                <TableCell className="numeric text-right font-medium">{row.score}</TableCell>
                <TableCell>
                  <Badge variant={row.badge}>
                    {row.badge.charAt(0).toUpperCase() + row.badge.slice(1)}
                  </Badge>
                </TableCell>
                <TableCell className="numeric text-right">{row.years} yrs</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Section>
  )
}

function CardSection() {
  return (
    <Section title="Card">
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Senior backend engineer</CardTitle>
            <CardDescription>Acme Corp · Active</CardDescription>
            <CardAction>
              <Button variant="ghost" size="icon-sm" aria-label="Campaign actions">
                <MoreHorizontalIcon />
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="numeric flex gap-6">
            <div>
              <div className="text-xl font-semibold">128</div>
              <div className="text-xs text-muted-foreground">Applications</div>
            </div>
            <div>
              <div className="text-xl font-semibold">14</div>
              <div className="text-xs text-muted-foreground">Strong</div>
            </div>
          </CardContent>
          <CardFooter className="justify-end gap-2">
            <Button variant="outline" size="sm">
              Export
            </Button>
            <Button size="sm">Open</Button>
          </CardFooter>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardTitle>Small card</CardTitle>
            <CardDescription>size=&quot;sm&quot; for KPI strips.</CardDescription>
          </CardHeader>
          <CardContent className="numeric text-2xl font-semibold">87%</CardContent>
        </Card>
      </div>
    </Section>
  )
}

function SkeletonSection() {
  return (
    <Section title="Skeleton" description="Loading state for rows and cards.">
      <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading">
        {[0, 1, 2].map((index) => (
          <div key={index} className="flex items-center gap-3">
            <Skeleton className="size-8 rounded-full" />
            <div className="flex flex-1 flex-col gap-1.5">
              <Skeleton className="h-3 w-1/3" />
              <Skeleton className="h-3 w-1/2" />
            </div>
            <Skeleton className="h-5 w-16" />
          </div>
        ))}
      </div>
    </Section>
  )
}

function MiscSection() {
  return (
    <Section title="Separator, scroll area, kbd, breadcrumb">
      <div className="flex h-5 items-center gap-3 text-sm">
        <span>Campaigns</span>
        <Separator orientation="vertical" />
        <span>Talent</span>
        <Separator orientation="vertical" />
        <span>Settings</span>
      </div>
      <Separator />
      <ScrollArea className="h-32 w-56 rounded-md border">
        <div className="p-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">Skills</p>
          {[
            "Python",
            "FastAPI",
            "PostgreSQL",
            "Celery",
            "Redis",
            "TypeScript",
            "React",
            "Next.js",
            "Docker",
            "Kubernetes",
          ].map((skill) => (
            <div key={skill} className="border-b py-1.5 text-sm last:border-0">
              {skill}
            </div>
          ))}
        </div>
      </ScrollArea>
      <Row>
        <Kbd>Esc</Kbd>
        <KbdGroup>
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </KbdGroup>
        <KbdGroup>
          <Kbd>J</Kbd>
          <Kbd>K</Kbd>
        </KbdGroup>
      </Row>
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="#">Campaigns</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>Senior backend engineer</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
    </Section>
  )
}

function CommandSection() {
  return (
    <Section
      title="Command"
      description="Inline here; the shell opens it in a dialog with ⌘K / Ctrl+K."
    >
      <Command className="max-w-sm rounded-lg border">
        <CommandInput placeholder="Type a command…" />
        <CommandList>
          <CommandEmpty>No results found.</CommandEmpty>
          <CommandGroup heading="Go to">
            <CommandItem>
              <BriefcaseIcon />
              Campaigns
              <CommandShortcut>G C</CommandShortcut>
            </CommandItem>
            <CommandItem>
              <UsersIcon />
              Talent
            </CommandItem>
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="Actions">
            <CommandItem>
              <PlusIcon />
              New campaign
            </CommandItem>
            <CommandItem disabled>
              <DownloadIcon />
              Export (disabled)
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </Section>
  )
}

/* ------------------------------------------------------------------------ */
/* Overlays                                                                  */
/* ------------------------------------------------------------------------ */

function OverlaysSection() {
  const [showHidden, setShowHidden] = React.useState(true)
  const [density, setDensity] = React.useState("compact")
  const id = React.useId()

  return (
    <Section
      title="Dialog, sheet, popover, dropdown menu, tooltip"
      description="Portaled: they use the app theme, not this pane's."
    >
      <Row>
        <Dialog>
          <DialogTrigger asChild>
            <Button variant="outline">Open dialog</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete campaign?</DialogTitle>
              <DialogDescription>
                This removes the campaign and its 128 applications. People stay in
                Talent.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline">Cancel</Button>
              </DialogClose>
              <Button variant="destructive">
                <Trash2Icon data-icon="inline-start" />
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Sheet>
          <SheetTrigger asChild>
            <Button variant="outline">Open sheet (right)</Button>
          </SheetTrigger>
          <SheetContent className="sm:max-w-[480px]">
            <SheetHeader>
              <SheetTitle>Asha Verma</SheetTitle>
              <SheetDescription>Review panel: about 480px, keeps the table visible.</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-2 px-4">
              <Label htmlFor={`${id}-notes`}>Notes</Label>
              <Textarea id={`${id}-notes`} placeholder="Add a note…" />
            </div>
            <SheetFooter>
              <Button>Move to interview</Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>

        <Sheet>
          <SheetTrigger asChild>
            <Button variant="outline">Sheet (left)</Button>
          </SheetTrigger>
          <SheetContent side="left">
            <SheetHeader>
              <SheetTitle>Filters</SheetTitle>
              <SheetDescription>Left-hand sheet.</SheetDescription>
            </SheetHeader>
          </SheetContent>
        </Sheet>

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline">Open popover</Button>
          </PopoverTrigger>
          <PopoverContent>
            <PopoverHeader>
              <PopoverTitle>Score range</PopoverTitle>
              <PopoverDescription>Show applications scoring between:</PopoverDescription>
            </PopoverHeader>
            <div className="grid grid-cols-2 gap-2">
              <Input aria-label="Minimum score" defaultValue="60" />
              <Input aria-label="Maximum score" defaultValue="100" />
            </div>
          </PopoverContent>
        </Popover>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline">
              Dropdown menu
              <MoreHorizontalIcon data-icon="inline-end" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-56">
            <DropdownMenuLabel>Application</DropdownMenuLabel>
            <DropdownMenuGroup>
              <DropdownMenuItem>
                <CopyIcon />
                Copy email
                <DropdownMenuShortcut>⌘C</DropdownMenuShortcut>
              </DropdownMenuItem>
              <DropdownMenuItem disabled>
                <DownloadIcon />
                Download resume (disabled)
              </DropdownMenuItem>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>Move to stage</DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  <DropdownMenuItem>Screening</DropdownMenuItem>
                  <DropdownMenuItem>Interview</DropdownMenuItem>
                  <DropdownMenuItem>Offer</DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuCheckboxItem
              checked={showHidden}
              onCheckedChange={(checked) => setShowHidden(checked === true)}
            >
              Show rejected
            </DropdownMenuCheckboxItem>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Density</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={density} onValueChange={setDensity}>
              <DropdownMenuRadioItem value="compact">Compact</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="comfortable">Comfortable</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive">
              <Trash2Icon />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="outline">Hover or focus me</Button>
          </TooltipTrigger>
          <TooltipContent>
            Tooltip <Kbd>⌘K</Kbd>
          </TooltipContent>
        </Tooltip>
      </Row>
    </Section>
  )
}

function ToastsSection() {
  return (
    <Section title="Toasts (sonner)">
      <Row>
        <Button variant="outline" onClick={() => toast("Campaign saved")}>
          Default
        </Button>
        <Button variant="outline" onClick={() => toast.success("3 resumes processed")}>
          Success
        </Button>
        <Button variant="outline" onClick={() => toast.info("Processing continues in the background")}>
          Info
        </Button>
        <Button variant="outline" onClick={() => toast.warning("AI quota reached, retrying in 60 s")}>
          Warning
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            toast.error("Upload failed", { description: "resume.pdf is larger than 10 MB." })
          }
        >
          Error
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            toast.promise(new Promise((resolve) => setTimeout(resolve, 1500)), {
              loading: "Exporting CSV…",
              success: "Export ready",
              error: "Export failed",
            })
          }
        >
          Loading → success
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            toast("Application rejected", {
              action: { label: "Undo", onClick: () => toast("Restored") },
            })
          }
        >
          With action
        </Button>
      </Row>
    </Section>
  )
}

/* ------------------------------------------------------------------------ */
/* Form                                                                      */
/* ------------------------------------------------------------------------ */

const campaignSchema = z.object({
  title: z.string().min(3, "Title must be at least 3 characters."),
  role: z.string().min(2, "Role is required."),
  status: z.enum(["active", "closed"], { message: "Choose a status." }),
  notify: z.boolean(),
})

type CampaignValues = z.infer<typeof campaignSchema>

function FormSection() {
  const form = useForm<CampaignValues>({
    resolver: zodResolver(campaignSchema),
    defaultValues: { title: "", role: "", notify: true },
  })

  function onSubmit(values: CampaignValues) {
    toast.success(`Saved “${values.title}”`)
  }

  return (
    <Section
      title="Form (react-hook-form + zod)"
      description="Submit empty to see validation messages wired to aria-invalid and aria-describedby."
    >
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex max-w-md flex-col gap-4" noValidate>
          <FormField
            control={form.control}
            name="title"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Title</FormLabel>
                <FormControl>
                  <Input placeholder="Platform team hire" {...field} />
                </FormControl>
                <FormDescription>Shown in the campaign list.</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="role"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Role</FormLabel>
                <FormControl>
                  <Input placeholder="Senior backend engineer" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="status"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Status</FormLabel>
                <Select onValueChange={field.onChange} value={field.value}>
                  <FormControl>
                    <SelectTrigger className="w-40">
                      <SelectValue placeholder="Choose…" />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="closed">Closed</SelectItem>
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="notify"
            render={({ field }) => (
              <FormItem className="flex flex-row items-center gap-2">
                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={(checked) => field.onChange(checked === true)}
                  />
                </FormControl>
                <FormLabel>Notify me when processing finishes</FormLabel>
              </FormItem>
            )}
          />
          <div className="flex gap-2">
            <Button type="submit">Save campaign</Button>
            <Button type="button" variant="ghost" onClick={() => form.reset()}>
              Reset
            </Button>
          </div>
        </form>
      </Form>
    </Section>
  )
}
