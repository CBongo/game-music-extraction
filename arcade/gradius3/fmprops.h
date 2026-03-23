typedef unsigned char    byte;

struct fmopprops {
    byte detune1_multiplier;
    byte total_level;
    byte keyscale_attack;
    byte lfo_am_en_decay;
    byte detune2_susrate;
    byte suslevel_rel;
};

struct fmprops {
    byte op_conn_alg;
    struct fmopprops op1;
    struct fmopprops op3;
    struct fmopprops op2;
    struct fmopprops op4;
};

