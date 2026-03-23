typedef unsigned char   undefined;

typedef unsigned char    byte;
typedef unsigned int    dword;
typedef unsigned char    undefined1;
typedef unsigned short    undefined2;
typedef unsigned short    word;
typedef void * pointer;
typedef struct vstate vstate, *Pstruct;

/* note: this reflects the FM voice state; the 7232 uses fields 0x9 and after 
    in different ways */
struct vstate {
    byte voicenum; /* used as offset into ym2151 regs for per-voice settings */
    byte songnum;
    undefined field2_0x2;
    pointer callback;
    pointer vptr;
    pointer callback_7; /* set from callback_7_tbl[vstate 0xf][notenum] */
    byte field6_0x9;
    word duration; /* not fixed-point, actual dur value */
    undefined field8_0xc;
    undefined field9_0xd;
    byte octave_note_adj; /* added to notenum */
    byte callback_7_table_select;
    byte field12_0x10;
    byte level_adj; /* added to total levels */
    undefined field14_0x12;
    char fine_tuning_64ths; /* key code frac? */
    word base_notenum_plus_fine; /* notenum << 6 + fine tuning 64ths */
    undefined field17_0x16;
    word used_notenum_plus_fine;
    byte set_key_code_flag;
    byte transpose; /* added to notenum */
    undefined1 level_adj2; /* Created by retype action */
    byte field22_0x1c; /* added to notenum */
    byte field23_0x1d;
    byte counter_1e; /* counter for 1c/1d */
    byte pan_channel_flags; /* LR accum */
    byte repeat_count_for_f1; /* Created by retype action */
    pointer repeat_addr_for_f2; /* Created by retype action */
    byte repeat_count_for_f2; /* Created by retype action */
    pointer repeat_addr_for_f4;
    byte repeat_count_for_f4;
    pointer call_return_addr;
    pointer call_target_addr;
    byte call_repeat_count;
    byte fblevel_op_connect; /* lower 6 bits of reg 0x20 = feedback level + op conn */
    byte total_op_level_all;
    byte total_level_op1;
    byte total_level_op3;
    byte total_level_op2;
    byte total_level_op4;
    byte suslvl_rel_op1;
    byte suslvl_rel_op3;
    byte suslvl_rel_op2;
    byte suslvl_rel_op4;
    byte field44_0x36; /* set by vcmd ee */
    byte field45_0x37; /* reset to 0x36 on note start */
    byte field46_0x38; /* zeroed on note start */
    undefined field47_0x39;
    undefined field48_0x3a;
    undefined field49_0x3b;
    undefined field50_0x3c;
    undefined field51_0x3d;
    byte patch; /* index to fmprops array */
    byte muted;
    byte repeat_count_for_f8;
    byte k007232_volume;
    undefined field56_0x42;
    byte k007232_volume_adj;
    undefined field58_0x44;
    undefined field59_0x45;
    undefined field60_0x46;
    undefined field61_0x47;
    undefined field62_0x48;
    undefined field63_0x49;
    undefined field64_0x4a;
    undefined field65_0x4b;
    undefined field66_0x4c;
    undefined field67_0x4d;
    undefined field68_0x4e;
    undefined field69_0x4f;
};

