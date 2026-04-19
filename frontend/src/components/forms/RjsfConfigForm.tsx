import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";

interface Props {
  schema: object;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}

export function RjsfConfigForm({ schema, value, onChange }: Props) {
  return (
    <div className="rjsf-wrap rounded-md border bg-card p-4 text-sm">
      <Form
        schema={schema}
        validator={validator}
        formData={value}
        liveValidate
        showErrorList={false}
        onChange={(e) => onChange(e.formData as Record<string, unknown>)}
        uiSchema={{ "ui:submitButtonOptions": { norender: true } }}
      >
        {/* No submit — parent form owns submission */}
        <span />
      </Form>
    </div>
  );
}
