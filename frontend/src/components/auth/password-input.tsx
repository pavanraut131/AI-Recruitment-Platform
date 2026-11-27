"use client"

import { EyeIcon, EyeOffIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

/**
 * A password field with a "Show password" toggle (NIST SP 800-63B: let people
 * check what they typed instead of making them type it twice). Takes the
 * input's props, so it works inside `FormControl`.
 */
export function PasswordInput({ className, disabled, ...props }: Omit<React.ComponentProps<"input">, "type">) {
  const [visible, setVisible] = useState(false)
  return (
    <div className="relative">
      <Input type={visible ? "text" : "password"} className={cn("pr-10", className)} disabled={disabled} {...props} />
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Show password"
        aria-pressed={visible}
        disabled={disabled}
        onClick={() => setVisible((shown) => !shown)}
        className="absolute top-0.5 right-0.5 text-muted-foreground hover:text-foreground"
      >
        {visible ? <EyeOffIcon /> : <EyeIcon />}
      </Button>
    </div>
  )
}
