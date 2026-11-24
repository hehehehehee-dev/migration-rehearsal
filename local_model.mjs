/** Local OpenAI-compatible adapter for a GGUF model. It does not run any tools. */
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {getLlama, LlamaChatSession} from './.tooling/node_modules/node-llama-cpp/dist/index.js';

const [, , modelPath, port = '11455'] = process.argv;
if (!modelPath) throw new Error('Pass the GGUF model path.');
const llama = await getLlama({gpu: process.env.REHEARSAL_GPU === 'vulkan' ? 'vulkan' : false, build: 'never'});
const model = await llama.loadModel({modelPath});
const context = await model.createContext({contextSize: 8192, threads: 8, sequences: 1});

function dialogue(messages) {
    return messages.map(m => {
        const calls = m.tool_calls?.map(call => call.function.name).join(', ');
        return m.role + (m.name ? ' ' + m.name : '') + ': ' +
            (calls ? 'Called ' + calls + '\n' : '') + (m.content ?? '');
    }).join('\n\n');
}

function toolParameters(tool) {
    if (tool.name !== 'emit_tool_plan') return tool.parameters;
    const properties = tool.parameters.properties;
    return {...tool.parameters, properties: {
        ...properties,
        description: {...properties.description, maxLength: 180},
        body_source: {...properties.body_source, maxLength: 900},
        imports: {...properties.imports, maxItems: 10},
    }};
}

async function complete(body) {
    const tools = (body.tools ?? []).map(t => t.function);
    const forced = body.tool_choice?.function?.name;
    const required = forced ? tools.find(t => t.name === forced) :
        (body.tool_choice === 'required' && tools.length === 1 ? tools[0] : null);
    const shaping = required?.name === 'emit_tool_plan';
    const systemPrompt = shaping && body.messages.find(message => message.role === 'system')?.content;
    console.log("Model request",required?.name ?? (tools.length ? "agent choice" : "text"));
    const sequence = context.getSequence();
    const session = new LlamaChatSession({contextSequence: sequence, autoDisposeSequence: true,
        ...(systemPrompt ? {systemPrompt} : {})});
    try {
        let grammar, prompt;
        if (required) {
            const parameters = toolParameters(required);
            grammar = await llama.createGrammarForJsonSchema(parameters);
            const messages = systemPrompt ? body.messages.filter(message => message.role !== 'system') : body.messages;
            prompt = dialogue(messages) + '\n\nReturn only JSON arguments for the required function ' +
                required.name + '. Its parameter schema is ' + JSON.stringify(parameters);
        } else if (tools.length) {
            // The model selects the tool from the whole surface, or chooses to stop.
            const names = tools.map(t => t.name);
            if (body.tool_choice !== 'required') names.push('final');
            grammar = await llama.createGrammarForJsonSchema({
                type:'object', properties:{
                    action:{enum:names}, message:{type:'string', maxLength:body.tool_choice === 'required' ? 0 : 200},
                }, required:['action','message'], additionalProperties:false,
            });
            prompt = dialogue(body.messages) +
                '\n\nAvailable tools: ' + JSON.stringify(tools.map(t => [t.name,t.description])) +
                '\nChoose exactly one action. For an available tool, give its exact name and an empty message. ' +
                (body.tool_choice === 'required' ? '' :
                    'Use "final" with a message if you need clarification from the reviewer or have finished.') +
                '\nLatest instruction: ' + (body.messages.at(-1)?.content ?? '');
        } else {
            if (body.response_format?.type === 'json_object') grammar = await llama.getGrammarFor('json');
            prompt = dialogue(body.messages);
        }
        let text = '', completed;
        const abort = new AbortController();
        const tokenLimit = shaping ? 1400 : required ? 650 : 180;
        const response = await session.prompt(prompt, {
            signal: abort.signal, stopOnAbortSignal: true,
            onTextChunk(chunk) {
                text += chunk;
                if (text.length % 200 < chunk.length) console.log("Generated chars", text.length);
                if (grammar && text.trimEnd().endsWith('}')) {
                    try { completed = JSON.parse(text); abort.abort(); } catch {}
                }
            },
            maxTokens: Math.min(body.max_tokens ?? tokenLimit, tokenLimit),
            temperature:0, ...(grammar ? {grammar} : {}),
        });
        const prompt_tokens = sequence.tokenMeter.usedInputTokens;
        const completion_tokens = sequence.tokenMeter.usedOutputTokens;
        const usage = {prompt_tokens, completion_tokens, total_tokens:prompt_tokens + completion_tokens};
        let message;
        if (required) {
            const args = completed ?? JSON.parse(response);
            if (process.env.REHEARSAL_MODEL_TRACE === '1') console.log('Generated plan', JSON.stringify(args));
            message = toolMessage(required.name, args);
        } else if (tools.length) {
            const choice = completed ?? JSON.parse(response);
            message = choice.action === 'final' ? {role:'assistant',content:choice.message} :
                toolMessage(choice.action, {});
        } else {
            message = {role:'assistant',content:response};
        }
        return {message,usage};
    } finally {
        session.dispose();
    }
}

function toolMessage(name, args) {
    return {role:'assistant',content:null,tool_calls:[{
        type:'function',id:'call_' + randomUUID(),function:{name,arguments:JSON.stringify(args)},
    }]};
}

let queue = Promise.resolve();
createServer((req, res) => {
    const write = (code, packet) => {
        res.writeHead(code,{'Content-Type':'application/json'});
        res.end(JSON.stringify(packet));
    };
    if (req.method === 'GET' && req.url === '/health') return write(200,{status:'ok'});
    if (req.method !== 'POST' || req.url !== '/v1/chat/completions') return write(404,{error:'Unknown route'});
    let raw = '';
    req.setEncoding('utf8');
    req.on('data', part => { raw += part; if (raw.length > 2_000_000) req.destroy(); });
    req.on('end', () => {
        queue = queue.then(async () => {
            try {
                const input = JSON.parse(raw);
                const {message,usage} = await complete(input);
                console.log('Model response',message.tool_calls?.[0]?.function.name ?? 'text');
                write(200,{id:randomUUID(),object:'chat.completion',model:input.model,usage,
                    choices:[{index:0,message,finish_reason:message.tool_calls ? 'tool_calls' : 'stop'}]});
            } catch (e) {
                console.error(e);
                write(500,{error:{message:String(e)}});
            }
        });
    });
}).listen(Number(port),'127.0.0.1', () => console.log('Local model listening on',port));
